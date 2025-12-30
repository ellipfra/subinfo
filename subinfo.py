#!/usr/bin/env python3
"""
CLI tool to analyze TheGraph allocations and curation signals

Usage:
    subinfo <subgraph_hash>

The subgraph_hash is the IPFS hash of the subgraph you want to see information for.
The TheGraph Network subgraph URL must be configured via:
    - Environment variable: THEGRAPH_NETWORK_SUBGRAPH_URL
    - Config file: ~/.grtinfo/config.json (key "network_subgraph_url")

Example:
    export THEGRAPH_NETWORK_SUBGRAPH_URL="https://your-graph-node/subgraphs/id/QmNetworkSubgraphHash"
    subinfo QmYourSubgraphHash
"""

import sys
import json
import argparse
import os
import re
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
from pathlib import Path

# Import shared modules
from common import (
    Colors, terminal_link, format_deployment_link,
    format_tokens, format_timestamp, format_duration,
    print_section, strip_ansi, get_display_width
)
from config import get_network_subgraph_url, get_ens_subgraph_url, get_my_indexer_id
from ens_client import ENSClient
from sync_status import IndexerStatusClient, format_sync_status as _format_sync_status
from rewards import get_accrued_rewards, get_indexer_reward_cut
from logger import setup_logging, get_logger

log = get_logger(__name__)
    


class TheGraphClient:
    """Client to query TheGraph Network subgraph"""
    
    def __init__(self, subgraph_url: str):
        # The subgraph URL is directly the GraphQL endpoint
        self.subgraph_url = subgraph_url.rstrip('/')
        self._session = requests.Session()
        self._cache_file = Path.home() / '.grtinfo' / 'network_totals_cache.json'
        self._cache_duration = 3600  # Cache valid for 1 hour
    
    def is_network_subgraph(self) -> bool:
        """Check if this subgraph is the TheGraph Network subgraph"""
        try:
            # Check if required entities exist
            test_query = """
            {
                __type(name: "Allocation") {
                    name
                }
            }
            """
            result = self.query(test_query)
            return result.get('__type') is not None
        except:
            return False
    
    def query(self, query: str, variables: Optional[Dict] = None) -> Dict:
        """Execute a GraphQL query"""
        try:
            response = self._session.post(
                self.subgraph_url,
                json={'query': query, 'variables': variables or {}},
                headers={'Content-Type': 'application/json'},
                timeout=30
            )
            response.raise_for_status()
            data = response.json()
            if 'errors' in data:
                errors = data['errors']
                # Check if it's a schema problem (application subgraph instead of network)
                for error in errors:
                    if 'has no field' in error.get('message', '') and 'allocations' in error.get('message', ''):
                        raise Exception(
                            "This subgraph does not appear to be the TheGraph Network subgraph.\n"
                            "The 'subinfo' tool works only with the TheGraph Network subgraph\n"
                            "that contains information about allocations and curation signals.\n"
                            f"GraphQL error: {error.get('message', '')}"
                        )
                error_msg = json.dumps(errors, indent=2)
                raise Exception(f"GraphQL errors:\n{error_msg}")
            return data.get('data', {})
        except requests.exceptions.RequestException as e:
            raise Exception(f"HTTP request error: {e}")
    
    def get_subgraph_id(self, ipfs_hash: str) -> Optional[str]:
        """Get the subgraph ID from a deployment IPFS hash"""
        query = """
        query GetSubgraphId($ipfsHash: String!) {
            subgraphDeployments(where: { ipfsHash: $ipfsHash }, first: 1) {
                versions(first: 1, orderBy: createdAt, orderDirection: desc) {
                    subgraph {
                        id
                    }
                }
            }
        }
        """
        try:
            result = self.query(query, {'ipfsHash': ipfs_hash})
            deployments = result.get('subgraphDeployments', [])
            if deployments:
                versions = deployments[0].get('versions', [])
                if versions:
                    return versions[0].get('subgraph', {}).get('id')
        except:
            pass
        return None
    
    def get_current_allocations(self, subgraph_id: str) -> List[Dict]:
        """Get current allocations for a subgraph"""
        # First find the deployment ID if it's an IPFS hash
        deployment_id = subgraph_id
        if not deployment_id.startswith('0x'):
            # Search for subgraphDeployment by its IPFS hash
            deployment_query = """
            query FindSubgraphDeployment($ipfsHash: String!) {
                subgraphDeployments(
                    where: { ipfsHash: $ipfsHash }
                    first: 1
                ) {
                    id
                }
            }
            """
            try:
                deployment_result = self.query(deployment_query, {'ipfsHash': subgraph_id})
                deployments = deployment_result.get('subgraphDeployments', [])
                if not deployments:
                    return []
                deployment_id = deployments[0]['id']
            except:
                return []
        
        # Now search for allocations with the deployment ID
        query = """
        query GetCurrentAllocations($subgraphId: String!) {
            allocations(
                where: { subgraphDeployment: $subgraphId, status: Active }
                orderBy: createdAt
                orderDirection: desc
            ) {
                id
                indexer {
                    id
                }
                allocatedTokens
                createdAt
                closedAt
                status
                indexingRewards
                indexingIndexerRewards
                indexingDelegatorRewards
            }
        }
        """
        try:
            result = self.query(query, {'subgraphId': deployment_id})
            return result.get('allocations', [])
        except:
            return []
    
    def get_allocation_history(self, subgraph_id: str, hours: int = 48) -> List[Dict]:
        """Get allocation history (created) for the last N hours"""
        cutoff_time = int((datetime.now() - timedelta(hours=hours)).timestamp())
        
        # First find the deployment ID if necessary
        deployment_id = subgraph_id
        if not deployment_id.startswith('0x'):
            deployment_query = """
            query FindSubgraphDeployment($ipfsHash: String!) {
                subgraphDeployments(
                    where: { ipfsHash: $ipfsHash }
                    first: 1
                ) {
                    id
                }
            }
            """
            try:
                deployment_result = self.query(deployment_query, {'ipfsHash': subgraph_id})
                deployments = deployment_result.get('subgraphDeployments', [])
                if not deployments:
                    return []
                deployment_id = deployments[0]['id']
            except Exception as e:
                return []
        
        # Utiliser une query inline car les variables BigInt posent problème
        query = f"""
        {{
            allocations(
                where: {{ 
                    subgraphDeployment: "{deployment_id}"
                    createdAt_gte: {cutoff_time}
                }}
                orderBy: createdAt
                orderDirection: desc
            ) {{
                id
                indexer {{
                    id
                }}
                allocatedTokens
                createdAt
                closedAt
                status
            }}
        }}
        """
        try:
            result = self.query(query)
            return result.get('allocations', [])
        except Exception as e:
            return []
    
    def get_unallocations(self, subgraph_id: str, hours: int = 48) -> List[Dict]:
        """Get unallocations (closed allocations) for the last N hours"""
        cutoff_time = int((datetime.now() - timedelta(hours=hours)).timestamp())
        
        # First find the deployment ID if necessary
        deployment_id = subgraph_id
        if not deployment_id.startswith('0x'):
            deployment_query = """
            query FindSubgraphDeployment($ipfsHash: String!) {
                subgraphDeployments(
                    where: { ipfsHash: $ipfsHash }
                    first: 1
                ) {
                    id
                }
            }
            """
            try:
                deployment_result = self.query(deployment_query, {'ipfsHash': subgraph_id})
                deployments = deployment_result.get('subgraphDeployments', [])
                if not deployments:
                    return []
                deployment_id = deployments[0]['id']
            except Exception as e:
                return []
        
        # Search for closed allocations in the period
        query = f"""
        {{
            allocations(
                where: {{ 
                    subgraphDeployment: "{deployment_id}"
                    status: Closed
                    closedAt_gte: {cutoff_time}
                }}
                orderBy: closedAt
                orderDirection: desc
            ) {{
                id
                indexer {{
                    id
                }}
                allocatedTokens
                createdAt
                closedAt
                status
                indexingRewards
            }}
        }}
        """
        try:
            result = self.query(query)
            return result.get('allocations', [])
        except Exception as e:
            return []
    
    def get_poi_submissions(self, subgraph_id: str, hours: int = 48) -> List[Dict]:
        """Get POI submissions (reward collections) for the last N hours"""
        cutoff_time = int((datetime.now() - timedelta(hours=hours)).timestamp())
        
        # First find the deployment ID if necessary
        deployment_id = subgraph_id
        if not deployment_id.startswith('0x'):
            deployment_query = """
            query FindSubgraphDeployment($ipfsHash: String!) {
                subgraphDeployments(
                    where: { ipfsHash: $ipfsHash }
                    first: 1
                ) {
                    id
                }
            }
            """
            try:
                deployment_result = self.query(deployment_query, {'ipfsHash': subgraph_id})
                deployments = deployment_result.get('subgraphDeployments', [])
                if not deployments:
                    return []
                deployment_id = deployments[0]['id']
            except Exception as e:
                return []
        
        # Search for POI submissions in the period
        query = f"""
        {{
            poiSubmissions(
                where: {{ 
                    allocation_: {{ subgraphDeployment: "{deployment_id}" }}
                    presentedAtTimestamp_gte: {cutoff_time}
                }}
                orderBy: presentedAtTimestamp
                orderDirection: desc
            ) {{
                id
                presentedAtTimestamp
                poi
                allocation {{
                    id
                    status
                    indexer {{
                        id
                    }}
                    allocatedTokens
                    indexingRewards
                    createdAt
                }}
            }}
        }}
        """
        try:
            result = self.query(query)
            return result.get('poiSubmissions', [])
        except Exception as e:
            return []
    
    def get_indexers_stake_info(self, indexer_ids: List[str]) -> Dict[str, Dict]:
        """Get stake information for multiple indexers"""
        if not indexer_ids:
            return {}
        
        # Remove duplicates and format IDs
        unique_ids = list(set(id.lower() for id in indexer_ids if id))
        
        results = {}
        # Query in batches of 100
        batch_size = 100
        for i in range(0, len(unique_ids), batch_size):
            batch = unique_ids[i:i+batch_size]
            query = """
            query GetIndexersStake($ids: [String!]!) {
                indexers(where: { id_in: $ids }) {
                    id
                    stakedTokens
                    delegatedTokens
                    allocatedTokens
                }
            }
            """
            try:
                result = self.query(query, {'ids': batch})
                for indexer in result.get('indexers', []):
                    indexer_id = indexer.get('id', '').lower()
                    staked = float(indexer.get('stakedTokens', '0')) / 1e18
                    delegated = float(indexer.get('delegatedTokens', '0')) / 1e18
                    allocated = float(indexer.get('allocatedTokens', '0')) / 1e18
                    total_stake = staked + delegated
                    unallocated_pct = ((total_stake - allocated) / total_stake * 100) if total_stake > 0 else 0
                    results[indexer_id] = {
                        'staked': staked,
                        'delegated': delegated,
                        'total_stake': total_stake,
                        'allocated': allocated,
                        'unallocated_pct': unallocated_pct
                    }
            except:
                pass
        
        return results
    
    def get_indexers_urls(self, indexer_ids: List[str]) -> Dict[str, str]:
        """Get URLs for multiple indexers (fallback when ENS is not available)"""
        if not indexer_ids:
            return {}
        
        # Remove duplicates and format IDs
        unique_ids = list(set(id.lower() for id in indexer_ids if id))
        
        results = {}
        # Query in batches of 100
        batch_size = 100
        for i in range(0, len(unique_ids), batch_size):
            batch = unique_ids[i:i+batch_size]
            query = """
            query GetIndexersUrls($ids: [String!]!) {
                indexers(where: { id_in: $ids }) {
                    id
                    url
                }
            }
            """
            try:
                result = self.query(query, {'ids': batch})
                for indexer in result.get('indexers', []):
                    indexer_id = indexer.get('id', '').lower()
                    url = indexer.get('url')
                    if url:
                        results[indexer_id] = url
            except:
                pass
        
        return results
    
    def get_subgraph_metadata(self, subgraph_id: str) -> Optional[Dict]:
        """Get subgraph deployment metadata including network, grafting, and reward proportion"""
        # First find the deployment ID if it's an IPFS hash
        deployment_id = subgraph_id
        if not deployment_id.startswith('0x'):
            deployment_query = """
            query FindSubgraphDeployment($ipfsHash: String!) {
                subgraphDeployments(
                    where: { ipfsHash: $ipfsHash }
                    first: 1
                ) {
                    id
                }
            }
            """
            try:
                deployment_result = self.query(deployment_query, {'ipfsHash': subgraph_id})
                deployments = deployment_result.get('subgraphDeployments', [])
                if not deployments:
                    return None
                deployment_id = deployments[0]['id']
            except:
                return None
        
        # Query metadata
        query = """
        query GetSubgraphMetadata($deploymentId: String!) {
            subgraphDeployment(id: $deploymentId) {
                id
                ipfsHash
                signalAmount
                createdAt
                stakedTokens
                deniedAt
                indexingRewardAmount
                manifest {
                    network
                }
            }
        }
        """
        result = self.query(query, {'deploymentId': deployment_id})
        deployment = result.get('subgraphDeployment')
        
        if not deployment:
            return None
        
        # Get network from manifest
        network = None
        manifest = deployment.get('manifest')
        if manifest:
            network = manifest.get('network')
        
        # Get allocations and signal for this subgraph
        subgraph_allocations = deployment.get('stakedTokens', '0')
        subgraph_signal = deployment.get('signalAmount', '0')
        
        # Calculate reward proportion as ratio of (allocations/signal) vs network average
        # Reward proportion = (allocations_subgraph / signal_subgraph) / (total_allocations_network / total_signal_network) * 100
        reward_proportion = None
        try:
            # Try to load from cache file
            cache_data = None
            if self._cache_file.exists():
                try:
                    with open(self._cache_file, 'r') as f:
                        cache_data = json.load(f)
                    # Check if cache is still valid
                    cache_time = cache_data.get('timestamp', 0)
                    if datetime.now().timestamp() - cache_time > self._cache_duration:
                        cache_data = None  # Cache expired
                except:
                    cache_data = None
            
            if cache_data:
                # Use cached values
                network_total_allocations = cache_data.get('total_allocations', 0)
                network_total_signal = cache_data.get('total_signal', 0)
            else:
                # Query all subgraph deployments to get total allocations and total signal
                log.info("Calculating network totals (this may take a moment)...")
                network_total_allocations = 0
                network_total_signal = 0
                skip = 0
                batch_size = 1000
                batch_count = 0
                
                while True:
                    # Include all subgraphs (including inactive/disabled ones)
                    # because they dilute rewards in the actual smart contract
                    totals_query = f"""
                    query GetNetworkTotals {{
                        subgraphDeployments(
                            first: {batch_size}
                            skip: {skip}
                        ) {{
                            stakedTokens
                            signalAmount
                        }}
                    }}
                    """
                    totals_result = self.query(totals_query)
                    deployments = totals_result.get('subgraphDeployments', [])
                    
                    if not deployments:
                        break
                    
                    for dep in deployments:
                        allocations = dep.get('stakedTokens', '0')
                        signal = dep.get('signalAmount', '0')
                        try:
                            allocations_float = float(allocations) / 1e18
                            signal_float = float(signal) / 1e18
                            network_total_allocations += allocations_float
                            network_total_signal += signal_float
                        except:
                            pass
                    
                    batch_count += 1
                    if batch_count % 5 == 0:
                        log.debug(f"Fetched {batch_count * batch_size} deployments...")
                    
                    if len(deployments) < batch_size:
                        break
                    
                    skip += batch_size
                
                log.info(f"Network totals calculated: {network_total_allocations:,.0f} allocated, {network_total_signal:,.0f} signal")
                
                # Save to cache file
                try:
                    self._cache_file.parent.mkdir(parents=True, exist_ok=True)
                    with open(self._cache_file, 'w') as f:
                        json.dump({
                            'timestamp': datetime.now().timestamp(),
                            'total_allocations': network_total_allocations,
                            'total_signal': network_total_signal
                        }, f)
                except:
                    pass
            
            # Calculate ratios
            subgraph_allocations_float = float(subgraph_allocations) / 1e18
            subgraph_signal_float = float(subgraph_signal) / 1e18
            
            # Calculate reward proportion
            # Lower allocations/signal ratio = better yield (more rewards per allocation)
            # Formula: (network_ratio / subgraph_ratio) × 100
            # This means: if subgraph has lower ratio than network average, it yields > 100%
            if subgraph_signal_float > 0 and network_total_signal > 0:
                subgraph_ratio = subgraph_allocations_float / subgraph_signal_float
                network_ratio = network_total_allocations / network_total_signal
                
                if subgraph_ratio > 0:
                    # Invert: lower ratio = better yield
                    reward_proportion = (network_ratio / subgraph_ratio) * 100
                else:
                    reward_proportion = None
            else:
                reward_proportion = None
        except:
            pass
        
        return {
            'network': network,
            'rewardProportion': reward_proportion
        }
    
    def get_curation_signal(self, subgraph_id: str) -> Optional[Dict]:
        """Get current curation signal and check if it's a new deployment"""
        # Try first with subgraphDeployment
        query = """
        query GetCurationSignal($subgraphId: String!) {
            subgraphDeployment(id: $subgraphId) {
                id
                signalledTokens
                createdAt
            }
        }
        """
        result = self.query(query, {'subgraphId': subgraph_id})
        deployment = result.get('subgraphDeployment')
        
        # If not found, search by IPFS hash
        if not deployment:
            find_query = """
            query FindSubgraphDeployment($ipfsHash: String!) {
                subgraphDeployments(
                    where: { ipfsHash: $ipfsHash }
                    first: 1
                ) {
                    id
                    signalledTokens
                    createdAt
                }
            }
            """
            try:
                find_result = self.query(find_query, {'ipfsHash': subgraph_id})
                deployments = find_result.get('subgraphDeployments', [])
                if deployments:
                    deployment = deployments[0]
            except:
                pass
        
        if not deployment:
            return None
        
        # Check if it's a new deployment (created in the last 7 days)
        deployment_created_at = deployment.get('createdAt')
        is_new_deployment = False
        if deployment_created_at:
            deployment_age_days = (datetime.now().timestamp() - int(deployment_created_at)) / (24 * 3600)
            is_new_deployment = deployment_age_days <= 7
        
        # Get individual signals
        signals_query = """
        query GetSignals($deploymentId: String!) {
            signals(
                where: { subgraphDeployment: $deploymentId }
                first: 100
                orderBy: createdAt
                orderDirection: desc
            ) {
                id
                signaller {
                    id
                }
                signalledTokens
                createdAt
            }
        }
        """
        try:
            signals_result = self.query(signals_query, {'deploymentId': deployment['id']})
            signals = signals_result.get('signals', [])
        except:
            signals = []
        
        return {
            'signalledTokens': deployment.get('signalledTokens', '0'),
            'signals': signals,
            'isNewDeployment': is_new_deployment,
            'deploymentCreatedAt': deployment_created_at
        }
    
    def get_curation_signal_changes(self, subgraph_id: str, hours: int = 48) -> List[Dict]:
        """Get curation signal changes for the last N hours, including upgrades"""
        cutoff_time = int((datetime.now() - timedelta(hours=hours)).timestamp())
        
        # First find the deployment ID and check for upgrades
        deployment_id = subgraph_id
        old_deployment_id = None
        new_deployment_info = None
        
        if not deployment_id.startswith('0x'):
            # Find the deployment and check if subgraph was upgraded
            find_query = """
            query FindSubgraphDeployment($ipfsHash: String!) {
                subgraphDeployments(
                    where: { ipfsHash: $ipfsHash }
                    first: 1
                ) {
                    id
                    versions(first: 1) {
                        subgraph {
                            id
                            currentVersion {
                                subgraphDeployment {
                                    id
                                    ipfsHash
                                    createdAt
                                    signalAmount
                                }
                            }
                            versions(orderBy: createdAt, orderDirection: desc, first: 10) {
                                subgraphDeployment {
                                    id
                                    ipfsHash
                                    createdAt
                                    signalAmount
                                }
                            }
                        }
                    }
                }
            }
            """
            try:
                find_result = self.query(find_query, {'ipfsHash': subgraph_id})
                deployments = find_result.get('subgraphDeployments', [])
                if deployments:
                    old_deployment_id = deployments[0]['id']
                    versions = deployments[0].get('versions', [])
                    if versions:
                        subgraph = versions[0].get('subgraph', {})
                        current_version = subgraph.get('currentVersion', {})
                        current_deployment = current_version.get('subgraphDeployment', {})
                        current_dep_id = current_deployment.get('id')
                        current_dep_hash = current_deployment.get('ipfsHash')
                        
                        # Check if this is an old deployment and there's a newer one
                        if current_dep_id and current_dep_id != old_deployment_id:
                            current_dep_created = int(current_deployment.get('createdAt', '0'))
                            # Subgraph was upgraded, signal was transferred to new deployment
                            # Store new deployment info for upgrade detection (no time limit)
                            new_deployment_info = {
                                'id': current_dep_id,
                                'hash': current_dep_hash,
                                'created': current_dep_created,
                                'signal': current_deployment.get('signalAmount', '0')
                            }
                            deployment_id = current_dep_id  # Use current deployment for queries
                        else:
                            # Same deployment, no upgrade
                            deployment_id = old_deployment_id
                            new_deployment_info = None
                else:
                    return []
            except Exception as e:
                # If we can't find the deployment, return empty
                # Reset new_deployment_info if exception occurred
                new_deployment_info = None
                pass
        
        # Search for signal changes via SignalTransaction (captures both adds and removes)
        # Use inline query because BigInt variables cause issues
        query = f"""
        {{
            signalTransactions(
                where: {{ 
                    timestamp_gte: {cutoff_time}
                }}
                orderBy: timestamp
                orderDirection: desc
                first: 500
            ) {{
                id
                timestamp
                type
                signal {{
                    subgraphDeployment {{
                        id
                    }}
                    signalledTokens
                    curator {{
                        id
                    }}
                }}
            }}
        }}
        """
        try:
            result = self.query(query)
            transactions = result.get('signalTransactions', [])
            
            changes = []
            for tx in transactions:
                signal = tx.get('signal', {})
                if not signal or not isinstance(signal, dict):
                    continue
                
                tx_deployment = signal.get('subgraphDeployment', {})
                if not tx_deployment or tx_deployment.get('id') != deployment_id:
                    continue
                
                tx_type = tx.get('type', '')
                curator_id = 'Unknown'
                if signal.get('curator'):
                    curator_id = signal.get('curator', {}).get('id', 'Unknown')
                
                # Map transaction type to change type
                if tx_type in ['Signal', 'SignalAdded']:
                    change_type = 'signal'
                elif tx_type in ['SignalRemoved', 'SignalWithdrawn']:
                    change_type = 'unsignal'
                else:
                    change_type = 'signal'  # Default to signal
                
                changes.append({
                    'type': change_type,
                    'signaller': curator_id,
                    'tokens': signal.get('signalledTokens', '0'),
                    'timestamp': str(tx.get('timestamp', '0'))
                })
            
            # Check for subgraph upgrades (signal transfer to new deployment)
            # If we're viewing the OLD deployment, signal was transferred OUT (loss)
            if new_deployment_info is not None:
                # Add upgrade as signal change (only if not already added)
                upgrade_exists = any(c.get('type') == 'upgrade_out' and c.get('timestamp') == str(new_deployment_info['created']) for c in changes)
                if not upgrade_exists:
                    new_hash = new_deployment_info['hash']
                    new_subgraph_id = self.get_subgraph_id(new_hash) if new_hash != 'Unknown' else None
                    # Get signal from the OLD deployment (what was transferred out)
                    old_signal_query = f"""
                    {{
                        subgraphDeployment(id: "{old_deployment_id}") {{
                            signalAmount
                        }}
                    }}
                    """
                    try:
                        old_signal_result = self.query(old_signal_query)
                        old_signal = old_signal_result.get('subgraphDeployment', {}).get('signalAmount', '0')
                    except:
                        old_signal = new_deployment_info['signal']  # Fallback to new signal
                    
                    changes.insert(0, {  # Insert at beginning to show upgrade first
                        'type': 'upgrade_out',  # Signal transferred OUT of this deployment
                        'signaller': 'Subgraph Upgrade',
                        'tokens': old_signal if old_signal != '0' else new_deployment_info['signal'],
                        'timestamp': str(new_deployment_info['created']),
                        'new_deployment_hash': new_hash,
                        'new_subgraph_id': new_subgraph_id
                    })
            elif old_deployment_id and old_deployment_id != deployment_id:
                # Fallback: if new_deployment_info wasn't set but we know there's an upgrade
                # This can happen if the first query didn't find the currentVersion correctly
                try:
                    upgrade_query = """
                    query FindUpgrade($oldDeploymentId: String!, $newDeploymentId: String!) {
                        oldDeployment: subgraphDeployment(id: $oldDeploymentId) {
                            id
                            ipfsHash
                            signalAmount
                        }
                        newDeployment: subgraphDeployment(id: $newDeploymentId) {
                            id
                            ipfsHash
                            createdAt
                            signalAmount
                        }
                    }
                    """
                    upgrade_result = self.query(upgrade_query, {
                        'oldDeploymentId': old_deployment_id,
                        'newDeploymentId': deployment_id
                    })
                    old_deployment = upgrade_result.get('oldDeployment', {})
                    new_deployment = upgrade_result.get('newDeployment', {})
                    if new_deployment:
                        new_dep_created = int(new_deployment.get('createdAt', '0'))
                        # Use old deployment's signal (what was transferred out)
                        old_signal = old_deployment.get('signalAmount', '0') if old_deployment else '0'
                        new_signal = new_deployment.get('signalAmount', '0')
                        # Add upgrade as signal change (only if not already added)
                        upgrade_exists = any(c.get('type') == 'upgrade_out' and c.get('timestamp') == str(new_dep_created) for c in changes)
                        if not upgrade_exists:
                            new_hash = new_deployment.get('ipfsHash', 'Unknown')
                            new_subgraph_id = self.get_subgraph_id(new_hash) if new_hash != 'Unknown' else None
                            changes.insert(0, {  # Insert at beginning to show upgrade first
                                'type': 'upgrade_out',  # Signal transferred OUT of this deployment
                                'signaller': 'Subgraph Upgrade',
                                'tokens': old_signal if old_signal != '0' else new_signal,
                                'timestamp': str(new_dep_created),
                                'new_deployment_hash': new_hash,
                                'new_subgraph_id': new_subgraph_id
                            })
                except Exception as e:
                    # Silently fail, upgrade detection is optional
                    pass
            
            # Sort by timestamp descending
            changes.sort(key=lambda x: int(x['timestamp']), reverse=True)
            return changes
        except Exception as e:
            # Fallback to signals if SignalTransaction fails
            try:
                query_signals = f"""
                {{
                    signals(
                        where: {{ 
                            subgraphDeployment: "{deployment_id}"
                            createdAt_gte: {cutoff_time}
                        }}
                        orderBy: createdAt
                        orderDirection: desc
                    ) {{
                        id
                        curator {{
                            id
                        }}
                        signalledTokens
                        createdAt
                    }}
                }}
                """
                result_signals = self.query(query_signals)
                signals = result_signals.get('signals', [])
                
                changes = []
                for sig in signals:
                    curator_id = 'Unknown'
                    if sig.get('curator'):
                        curator_id = sig.get('curator', {}).get('id', 'Unknown')
                    changes.append({
                        'type': 'signal',
                        'signaller': curator_id,
                        'tokens': sig.get('signalledTokens', '0'),
                        'timestamp': str(sig.get('createdAt', '0'))
                    })
                
                changes.sort(key=lambda x: int(x['timestamp']), reverse=True)
                return changes
            except:
                return []




def print_subgraph_metadata(metadata: Optional[Dict]):
    """Display subgraph metadata"""
    if not metadata:
        return
    
    print_section("Subgraph Metadata")
    
    # Network
    network = metadata.get('network')
    if network:
        print(f"{Colors.BOLD}Network:{Colors.RESET} {Colors.CYAN}{network}{Colors.RESET}")
    else:
        print(f"{Colors.BOLD}Network:{Colors.RESET} {Colors.DIM}Unknown{Colors.RESET}")
    
    # Reward proportion
    reward_proportion = metadata.get('rewardProportion')
    if reward_proportion is not None:
        print(f"{Colors.BOLD}Reward Proportion:{Colors.RESET} {Colors.BRIGHT_CYAN}{reward_proportion:.2f}%{Colors.RESET}")


def fetch_sync_statuses_async(indexer_urls: Dict[str, str], subgraph_hash: str, executor: ThreadPoolExecutor) -> Dict:
    """Start fetching sync statuses in background, returns a dict with futures"""
    if not indexer_urls or not subgraph_hash:
        return {'futures': {}, 'subgraph_hash': subgraph_hash}
    
    def fetch_indexer_status(indexer_id: str, url: str) -> Tuple[str, Optional[Dict], Optional[str]]:
        """Fetch status for a single indexer, returns (indexer_id, status, error)"""
        if not url:
            return (indexer_id, None, None)
        client = IndexerStatusClient(timeout=10)
        all_statuses = client.get_all_deployments_status(url)
        if all_statuses:
            status = all_statuses.get(subgraph_hash)
            return (indexer_id, status, None)
        return (indexer_id, None, client.last_error)
    
    futures = {
        executor.submit(fetch_indexer_status, indexer_id, url): indexer_id
        for indexer_id, url in indexer_urls.items()
    }
    return {'futures': futures, 'subgraph_hash': subgraph_hash}


def collect_sync_statuses(async_context: Optional[Dict], timeout: float = 5.0) -> Tuple[Dict, Dict]:
    """Collect sync statuses from async context, with timeout
    
    Returns: (sync_statuses dict, sync_errors dict)
        sync_statuses: indexer_id -> status dict
        sync_errors: indexer_id -> error string (short reason)
    """
    sync_statuses = {}
    sync_errors = {}
    
    if not async_context or not async_context.get('futures'):
        return sync_statuses, sync_errors
    
    futures = async_context['futures']
    completed_indexers = set()
    
    # Wait for futures with timeout - collect what's ready
    try:
        for future in as_completed(futures, timeout=timeout):
            try:
                indexer_id, status, error = future.result(timeout=0.1)
                completed_indexers.add(indexer_id)
                if status:
                    sync_statuses[indexer_id] = status
                elif error:
                    # Shorten common errors
                    short_error = error
                    if 'timeout' in error.lower() or 'timed out' in error.lower():
                        short_error = 'timeout'
                    elif '404' in error or 'not found' in error.lower():
                        short_error = 'no endpoint'
                    elif '403' in error or 'forbidden' in error.lower():
                        short_error = 'forbidden'
                    elif 'connection' in error.lower() or 'connect' in error.lower():
                        short_error = 'unreachable'
                    elif 'ssl' in error.lower() or 'certificate' in error.lower():
                        short_error = 'SSL error'
                    elif len(error) > 15:
                        short_error = error[:12] + '...'
                    sync_errors[indexer_id] = short_error
            except Exception:
                pass
    except TimeoutError:
        # Mark remaining futures as timed out
        for future, indexer_id in futures.items():
            if indexer_id not in completed_indexers and indexer_id not in sync_statuses:
                sync_errors[indexer_id] = 'timeout'
        log.debug(f"Sync status timeout: collected {len(sync_statuses)} statuses, {len(sync_errors)} errors")
    
    return sync_statuses, sync_errors


def print_allocations(allocations: List[Dict], title: str, my_indexer_id: Optional[str] = None, ens_client: Optional[ENSClient] = None, indexer_urls: Optional[Dict[str, str]] = None, reward_proportion: Optional[float] = None, network_url: Optional[str] = None, subgraph_hash: Optional[str] = None, sync_statuses: Optional[Dict] = None, sync_errors: Optional[Dict] = None) -> Tuple[List[Dict], int]:
    """Display allocations in a compact format with colors
    
    sync_statuses: pre-collected sync statuses dict (indexer_id -> status), or None to skip sync display
    sync_errors: dict of indexer_id -> short error reason
    
    Returns: (allocation_lines, lines_after_allocations)
        allocation_lines: list of allocation info dicts (for compatibility, but not used anymore)
        lines_after_allocations: count of lines printed after allocation lines (Total, Your Allocation, etc.)
    """
    print_section(title)
    if not allocations:
        print(f"{Colors.DIM}No allocations found.{Colors.RESET}")
        return [], 0
    
    # Track lines printed after allocation lines
    lines_after = 0
    allocation_lines = []  # Keep for compatibility but not used
    
    # Resolve ENS names in batch
    indexer_addresses = [alloc.get('indexer', {}).get('id', '') for alloc in allocations]
    ens_names = {}
    if ens_client:
        ens_names = ens_client.resolve_addresses_batch(indexer_addresses)
    
    # Use provided sync statuses/errors or empty dicts
    if sync_statuses is None:
        sync_statuses = {}
    if sync_errors is None:
        sync_errors = {}
    
    # Track allocation lines for later updates
    allocation_lines = []
    
    total = 0
    my_accrued_rewards = None
    my_allocation_amount = 0.0
    my_allocation_days = 0.0
    my_allocation_id = None
    
    for alloc in allocations:
        indexer = alloc.get('indexer', {})
        indexer_id = indexer.get('id', 'Unknown')
        tokens = alloc.get('allocatedTokens', '0')
        amount = float(tokens) / 1e18
        total += amount
        
        # Check if it's my indexer
        is_mine = my_indexer_id and indexer_id.lower() == my_indexer_id.lower()
        if is_mine:
            marker = f"{Colors.BRIGHT_YELLOW}★{Colors.RESET}"
            indexer_color = Colors.BRIGHT_YELLOW
            
            # Get accrued rewards from smart contract
            allocation_id = alloc.get('id', '')
            created_ts = int(alloc.get('createdAt', '0'))
            if created_ts > 0:
                my_allocation_days = (datetime.now().timestamp() - created_ts) / 86400
                my_allocation_amount = amount
                my_allocation_id = allocation_id
                
                # Try to get real rewards from contract
                if allocation_id:
                    my_accrued_rewards = get_accrued_rewards(allocation_id)
        else:
            marker = " "
            indexer_color = Colors.WHITE
        
        # Get ENS name if available, or URL as fallback
        ens_name = ens_names.get(indexer_id.lower()) if ens_names else None
        url = indexer_urls.get(indexer_id.lower()) if indexer_urls and not ens_name else None
        indexer_display = format_indexer_display(indexer_id, ens_name, url)
        
        created = format_timestamp(str(alloc.get('createdAt', '0')))[:16]  # YYYY-MM-DD HH:MM
        status = alloc.get('status', 'Active')
        status_color = Colors.BRIGHT_GREEN if status == 'Active' else Colors.DIM
        tokens_str = format_tokens(tokens)
        
        # Calculate duration for active allocations
        duration_str = ""
        if status == 'Active' and not alloc.get('closedAt'):
            created_ts = int(alloc.get('createdAt', '0'))
            if created_ts > 0:
                duration_seconds = datetime.now().timestamp() - created_ts
                duration_str = f" ({format_duration(duration_seconds)})"
        
        # Get sync status for this indexer
        sync_status = sync_statuses.get(indexer_id.lower())
        sync_error = sync_errors.get(indexer_id.lower()) if sync_errors else None
        if sync_status:
            sync_indicator = f"  {format_sync_status(sync_status)}"
        elif sync_error:
            sync_indicator = f"  {Colors.DIM}({sync_error}){Colors.RESET}"
        elif indexer_urls and not indexer_urls.get(indexer_id.lower()):
            sync_indicator = f"  {Colors.DIM}(no URL){Colors.RESET}"
        else:
            sync_indicator = ""
        
        # Calculate padding accounting for ANSI codes
        marker_width = get_display_width(marker)
        indexer_display_width = get_display_width(indexer_display)
        tokens_str_width = get_display_width(tokens_str)
        
        # Target widths: marker=1, indexer=32, tokens=17, date=16
        marker_padding = max(0, 1 - marker_width)
        indexer_padding = max(0, 32 - indexer_display_width)
        tokens_padding = max(0, 17 - tokens_str_width)
        
        if alloc.get('closedAt'):
            closed = format_timestamp(str(alloc.get('closedAt', '0')))[:16]
            print(f"  {marker}{' ' * marker_padding}  {indexer_color}{indexer_display}{' ' * indexer_padding}{Colors.RESET}  {Colors.BRIGHT_GREEN}{' ' * tokens_padding}{tokens_str}{Colors.RESET}  {Colors.DIM}{created}{Colors.RESET}  {status_color}{status}{Colors.RESET}")
        else:
            print(f"  {marker}{' ' * marker_padding}  {indexer_color}{indexer_display}{' ' * indexer_padding}{Colors.RESET}  {Colors.BRIGHT_GREEN}{' ' * tokens_padding}{tokens_str}{Colors.RESET}  {Colors.DIM}{created}{Colors.RESET}  {status_color}{status}{Colors.RESET}{Colors.DIM}{duration_str}{Colors.RESET}{sync_indicator}")
            # Track for compatibility (not used anymore)
            allocation_lines.append({
                'indexer_id': indexer_id.lower(),
                'base_line': ''  # Not needed anymore
            })
    
    print(f"{Colors.BOLD}Total: {Colors.BRIGHT_GREEN}{format_tokens(str(int(total * 1e18)))}{Colors.RESET}")
    lines_after += 1  # Total line
    
    # Display accrued rewards for my allocation
    if my_indexer_id and my_allocation_id:
        print(f"{Colors.BOLD}Your Allocation:{Colors.RESET}")
        lines_after += 1
        print(f"  {Colors.BRIGHT_YELLOW}★{Colors.RESET} Allocated: {Colors.BRIGHT_GREEN}{my_allocation_amount:,.0f} GRT{Colors.RESET} for {Colors.DIM}{my_allocation_days:.1f} days{Colors.RESET}")
        lines_after += 1
        if my_accrued_rewards is not None:
            if my_accrued_rewards > 0:
                print(f"  {Colors.BRIGHT_YELLOW}★{Colors.RESET} Accrued rewards: {Colors.BRIGHT_CYAN}{my_accrued_rewards:,.2f} GRT{Colors.RESET}")
                lines_after += 1
                
                # Get indexer reward cut and calculate split
                reward_cut = get_indexer_reward_cut(my_indexer_id, network_url) if network_url else None
                if reward_cut is not None:
                    indexer_share = my_accrued_rewards * reward_cut
                    delegator_share = my_accrued_rewards * (1 - reward_cut)
                    print(f"  {Colors.BRIGHT_YELLOW}★{Colors.RESET} Indexer share ({reward_cut*100:.1f}%): {Colors.BRIGHT_GREEN}{indexer_share:,.2f} GRT{Colors.RESET}")
                    lines_after += 1
                    print(f"  {Colors.BRIGHT_YELLOW}★{Colors.RESET} Delegator share ({(1-reward_cut)*100:.1f}%): {Colors.DIM}{delegator_share:,.2f} GRT{Colors.RESET}")
                    lines_after += 1
            else:
                print(f"  {Colors.BRIGHT_YELLOW}★{Colors.RESET} Accrued rewards: {Colors.DIM}0 GRT (no POI submitted yet){Colors.RESET}")
                lines_after += 1
        else:
            print(f"  {Colors.BRIGHT_YELLOW}★{Colors.RESET} Accrued rewards: {Colors.DIM}(install web3 for exact value){Colors.RESET}")
            lines_after += 1
    
    return allocation_lines, lines_after


def print_sync_status_summary(allocations: List[Dict], sync_statuses: Dict, ens_client: Optional[ENSClient] = None):
    """Print sync status summary for allocations"""
    if not allocations or not sync_statuses:
        return
    
    print_section("Sync Status")
    
    # Group by status type
    synced = []
    syncing = []
    failed = []
    unknown = []
    
    for alloc in allocations:
        indexer = alloc.get('indexer', {})
        indexer_id = indexer.get('id', '')
        status = sync_statuses.get(indexer_id.lower())
        
        if not status:
            continue
        
        # Get display name
        ens_name = None
        if ens_client:
            ens_name = ens_client.resolve_address(indexer_id)
        display_name = ens_name if ens_name else f"{indexer_id[:10]}.."
        
        health = status.get('health', '')
        synced_flag = status.get('synced', False)
        blocks_behind = 0
        
        chains = status.get('chains', [])
        if chains and len(chains) > 0:
            chain = chains[0]
            latest = chain.get('latestBlock', {})
            head = chain.get('chainHeadBlock', {})
            if latest and head:
                latest_num = int(latest.get('number', 0))
                head_num = int(head.get('number', 0))
                blocks_behind = head_num - latest_num
        
        if health == 'failed':
            failed.append((display_name, status))
        elif synced_flag and blocks_behind < 100:
            synced.append((display_name, status))
        elif blocks_behind > 0:
            syncing.append((display_name, blocks_behind))
        else:
            unknown.append((display_name, status))
    
    # Print summary
    if synced:
        names = [n for n, _ in synced[:5]]
        more = f" +{len(synced)-5}" if len(synced) > 5 else ""
        print(f"  {Colors.BRIGHT_GREEN}✓ Synced:{Colors.RESET} {', '.join(names)}{more}")
    
    if syncing:
        for name, behind in syncing[:5]:
            behind_str = f"{behind/1000:.0f}k" if behind >= 1000 else str(behind)
            print(f"  {Colors.BRIGHT_YELLOW}↻ {name}:{Colors.RESET} {Colors.DIM}-{behind_str} blocks{Colors.RESET}")
        if len(syncing) > 5:
            print(f"  {Colors.DIM}  ...and {len(syncing)-5} more syncing{Colors.RESET}")
    
    if failed:
        for name, status in failed[:3]:
            error = status.get('fatalError', {}).get('message', 'Unknown error')[:50]
            print(f"  {Colors.BRIGHT_RED}✗ {name}:{Colors.RESET} {Colors.DIM}{error}{Colors.RESET}")


def print_allocations_timeline(allocations: List[Dict], unallocations: List[Dict], poi_submissions: List[Dict] = None, hours: int = 48, my_indexer_id: Optional[str] = None, ens_client: Optional[ENSClient] = None, indexers_stake_info: Optional[Dict] = None, indexer_urls: Optional[Dict[str, str]] = None):
    """Display allocations, unallocations and reward collections in a chronological timeline with colors"""
    print_section(f"Allocations/Unallocations Timeline ({hours}h)")
    
    # Create a combined list with event type
    events = []
    
    # Add allocations (created)
    for alloc in allocations:
        indexer = alloc.get('indexer', {})
        events.append({
            'type': 'allocation',
            'timestamp': int(alloc.get('createdAt', '0')),
            'indexer': indexer.get('id', 'Unknown'),
            'tokens': alloc.get('allocatedTokens', '0'),
            'status': alloc.get('status', 'Active'),
            'closedAt': alloc.get('closedAt')
        })
    
    # Add unallocations (closed)
    for unalloc in unallocations:
        indexer = unalloc.get('indexer', {})
        events.append({
            'type': 'unallocation',
            'timestamp': int(unalloc.get('closedAt', '0')),
            'indexer': indexer.get('id', 'Unknown'),
            'tokens': unalloc.get('allocatedTokens', '0'),
            'status': unalloc.get('status', 'Closed'),
            'createdAt': unalloc.get('createdAt', '0'),
            'rewards': int(unalloc.get('indexingRewards', '0'))
        })
    
    # Add POI submissions (reward collections) for active allocations
    if poi_submissions:
        for poi in poi_submissions:
            alloc = poi.get('allocation', {})
            # Only show collections for allocations that are still active
            if alloc.get('status') == 'Active':
                indexer = alloc.get('indexer', {})
                rewards = int(alloc.get('indexingRewards', '0'))
                events.append({
                    'type': 'collect',
                    'timestamp': int(poi.get('presentedAtTimestamp', '0')),
                    'indexer': indexer.get('id', 'Unknown'),
                    'tokens': alloc.get('allocatedTokens', '0'),
                    'rewards': rewards,
                    'status': 'Active'
                })
    
    if not events:
        print(f"{Colors.DIM}No events found.{Colors.RESET}")
        return
    
    # Resolve ENS names in batch
    indexer_addresses = [event['indexer'] for event in events]
    ens_names = {}
    if ens_client:
        ens_names = ens_client.resolve_addresses_batch(indexer_addresses)
    
    # Sort by timestamp descending (most recent first)
    events.sort(key=lambda x: x['timestamp'], reverse=True)
    
    total_allocated = 0
    total_unallocated = 0
    
    for event in events:
        indexer_id = event['indexer']
        is_mine = my_indexer_id and indexer_id.lower() == my_indexer_id.lower()
        if is_mine:
            marker = f"{Colors.BRIGHT_YELLOW}★{Colors.RESET}"
            indexer_color = Colors.BRIGHT_YELLOW
        else:
            marker = " "
            indexer_color = Colors.WHITE
        
        # Get ENS name if available, or URL as fallback
        ens_name = ens_names.get(indexer_id.lower()) if ens_names else None
        url = indexer_urls.get(indexer_id.lower()) if indexer_urls and not ens_name else None
        indexer_display = format_indexer_display(indexer_id, ens_name, url)
        
        tokens = event['tokens']
        amount = float(tokens) / 1e18
        timestamp = format_timestamp(str(event['timestamp']))[:16]
        tokens_str = format_tokens(tokens)
        
        # Determine symbol and color based on event type
        if event['type'] == 'allocation':
            total_allocated += amount
            symbol = f"{Colors.BRIGHT_GREEN}+{Colors.RESET}"
            status = event['status']
            status_color = Colors.BRIGHT_GREEN if status == 'Active' else Colors.DIM
        elif event['type'] == 'collect':
            # Reward collection - don't add to totals, it's not a new allocation
            symbol = f"{Colors.BRIGHT_CYAN}${Colors.RESET}"
            status = "Collect"
            status_color = Colors.BRIGHT_CYAN
        else:  # unallocation
            total_unallocated += amount
            symbol = f"{Colors.BRIGHT_RED}-{Colors.RESET}"
            status_color = Colors.BRIGHT_RED
        
        # Calculate padding accounting for ANSI codes
        symbol_width = get_display_width(symbol)
        marker_width = get_display_width(marker)
        indexer_display_width = get_display_width(indexer_display)
        tokens_str_width = get_display_width(tokens_str)
        
        # Target widths: symbol=1, marker=1, indexer=32, tokens=17, date=16
        symbol_padding = max(0, 1 - symbol_width)
        marker_padding = max(0, 1 - marker_width)
        indexer_padding = max(0, 32 - indexer_display_width)
        tokens_padding = max(0, 17 - tokens_str_width)
        
        if event['type'] == 'allocation':
            if event.get('closedAt'):
                closed = format_timestamp(str(event['closedAt']))[:16]
                print(f"  [{symbol}]{' ' * symbol_padding} {marker}{' ' * marker_padding}  {Colors.DIM}{timestamp}{Colors.RESET}  {indexer_color}{indexer_display}{' ' * indexer_padding}{Colors.RESET}  {Colors.BRIGHT_GREEN}{' ' * tokens_padding}{tokens_str}{Colors.RESET}  {status_color}{status}{Colors.RESET} → closed {Colors.DIM}{closed}{Colors.RESET}")
            else:
                print(f"  [{symbol}]{' ' * symbol_padding} {marker}{' ' * marker_padding}  {Colors.DIM}{timestamp}{Colors.RESET}  {indexer_color}{indexer_display}{' ' * indexer_padding}{Colors.RESET}  {Colors.BRIGHT_GREEN}{' ' * tokens_padding}{tokens_str}{Colors.RESET}  {status_color}{status}{Colors.RESET}")
        elif event['type'] == 'collect':
            # Show reward collection with collected amount
            rewards = event.get('rewards', 0) / 1e18
            rewards_str = f"{rewards:,.2f} GRT collected"
            print(f"  [{symbol}]{' ' * symbol_padding} {marker}{' ' * marker_padding}  {Colors.DIM}{timestamp}{Colors.RESET}  {indexer_color}{indexer_display}{' ' * indexer_padding}{Colors.RESET}  {Colors.BRIGHT_CYAN}{' ' * tokens_padding}{tokens_str}{Colors.RESET}  {status_color}{rewards_str}{Colors.RESET}")
        else:  # unallocation
            created = format_timestamp(str(event.get('createdAt', '0')))[:16]
            # Show rewards collected at close
            rewards = event.get('rewards', 0) / 1e18
            rewards_info = f" → {Colors.BRIGHT_CYAN}{rewards:,.2f} GRT{Colors.RESET}" if rewards > 0 else ""
            # Check if indexer has high unallocated stake
            warning = ""
            if indexers_stake_info:
                stake_info = indexers_stake_info.get(indexer_id.lower())
                if stake_info and stake_info.get('unallocated_pct', 0) > 30:
                    unalloc_pct = stake_info['unallocated_pct']
                    warning = f" {Colors.BRIGHT_YELLOW}⚠ {unalloc_pct:.0f}% unallocated{Colors.RESET}"
            print(f"  [{symbol}]{' ' * symbol_padding} {marker}{' ' * marker_padding}  {Colors.DIM}{timestamp}{Colors.RESET}  {indexer_color}{indexer_display}{' ' * indexer_padding}{Colors.RESET}  {Colors.BRIGHT_RED}{' ' * tokens_padding}{tokens_str}{Colors.RESET}  closed{rewards_info}{warning}")
    
    print(f"{Colors.BOLD}Total:{Colors.RESET} {Colors.BRIGHT_GREEN}+{total_allocated:,.0f}{Colors.RESET} | {Colors.BRIGHT_RED}-{total_unallocated:,.0f} GRT{Colors.RESET}")


def print_curation_signal(signal_data: Optional[Dict]):
    """Display curation signal in a compact format with colors"""
    print_section("Curation Signal")
    if not signal_data:
        print(f"{Colors.DIM}No curation signal found.{Colors.RESET}")
        return
    
    signal_amount = format_tokens(signal_data.get('signalledTokens', '0'))
    print(f"{Colors.BOLD}Total signal:{Colors.RESET} {Colors.BRIGHT_CYAN}{signal_amount}{Colors.RESET}")
    
    # Display if it's a new deployment
    if signal_data.get('isNewDeployment'):
        deployment_created_at = signal_data.get('deploymentCreatedAt')
        if deployment_created_at:
            created_date = format_timestamp(str(deployment_created_at))[:16]
            print(f"{Colors.BRIGHT_YELLOW}⚠️  New deployment{Colors.RESET} (created {Colors.DIM}{created_date}{Colors.RESET})")
    
    signals = signal_data.get('signals', [])
    if signals:
        print(f"{Colors.DIM}Signals: {len(signals)}{Colors.RESET}")


def print_signal_changes(changes: List[Dict], hours: int = 48):
    """Display signal changes in a compact format with colors"""
    print_section(f"Signal Changes ({hours}h)")
    if not changes:
        print(f"{Colors.DIM}No changes found.{Colors.RESET}")
        return
    
    total_added = 0
    total_removed = 0
    
    for change in changes:
        change_type = change.get('type', 'unknown')
        tokens = change.get('tokens', '0')
        amount = float(tokens) / 1e18
        signaller = change.get('signaller', 'Unknown')
        signaller_short = signaller[:10] + "..." if len(signaller) > 10 else signaller
        timestamp = format_timestamp(str(change.get('timestamp', '0')))[:16]
        
        if change_type == 'upgrade_out':
            # Signal was transferred OUT to a new deployment (loss for this deployment)
            total_removed += amount
            symbol = f"{Colors.BRIGHT_RED}↑{Colors.RESET}"
            token_color = Colors.BRIGHT_RED
            new_hash = change.get('new_deployment_hash', 'Unknown')
            new_subgraph_id = change.get('new_subgraph_id')
            new_hash_link = format_deployment_link(new_hash, new_subgraph_id) if new_hash != 'Unknown' else new_hash
            signaller_display = f"{Colors.BRIGHT_RED}Upgraded → {new_hash_link}{Colors.RESET}"
        elif change_type == 'upgrade':
            # Signal was transferred IN from old deployment (gain for this deployment)
            total_added += amount
            symbol = f"{Colors.BRIGHT_GREEN}↑{Colors.RESET}"
            token_color = Colors.BRIGHT_GREEN
            new_hash = change.get('new_deployment_hash', 'Unknown')
            new_subgraph_id = change.get('new_subgraph_id')
            new_hash_link = format_deployment_link(new_hash, new_subgraph_id) if new_hash != 'Unknown' else new_hash
            signaller_display = f"{Colors.BRIGHT_GREEN}Upgrade ← {new_hash_link}{Colors.RESET}"
        elif change_type == 'signal':
            total_added += amount
            symbol = f"{Colors.BRIGHT_GREEN}+{Colors.RESET}"
            token_color = Colors.BRIGHT_GREEN
            signaller_display = signaller_short
        else:
            total_removed += amount
            symbol = f"{Colors.BRIGHT_RED}-{Colors.RESET}"
            token_color = Colors.BRIGHT_RED
            signaller_display = signaller_short
        
        tokens_str = format_tokens(tokens)
        if change_type in ('upgrade', 'upgrade_out'):
            # Upgrades have longer display (includes full Qm hash link), no extra padding
            print(f"  [{symbol}]  {Colors.DIM}{timestamp:16}{Colors.RESET}  {signaller_display}  {token_color}{tokens_str}{Colors.RESET}")
        else:
            print(f"  [{symbol}]  {Colors.DIM}{timestamp:16}{Colors.RESET}  {Colors.WHITE}{signaller_display:35}{Colors.RESET}  {token_color}{tokens_str:>18}{Colors.RESET}")
    
    net = total_added - total_removed
    net_color = Colors.BRIGHT_GREEN if net >= 0 else Colors.BRIGHT_RED
    print(f"{Colors.BOLD}Total:{Colors.RESET} {Colors.BRIGHT_GREEN}+{total_added:,.0f}{Colors.RESET} | {Colors.BRIGHT_RED}-{total_removed:,.0f}{Colors.RESET} | Net: {net_color}{net:,.0f} GRT{Colors.RESET}")


def format_indexer_display(indexer_id: str, ens_name: Optional[str] = None, url: Optional[str] = None, max_width: int = 32) -> str:
    """Format indexer display with ENS name or URL if available, truncated to max_width"""
    if ens_name:
        # Format: "ens_name (0x1234..)"
        addr_suffix = f" ({indexer_id[:6]}..)"
        max_ens_len = max_width - len(addr_suffix)
        if len(ens_name) > max_ens_len:
            ens_name = ens_name[:max_ens_len-2] + ".."
        return f"{ens_name}{addr_suffix}"
    
    if url:
        # Format: "..domain.com (0x1234..)" - cut from beginning
        # Remove protocol
        display_url = url
        for prefix in ['https://', 'http://', 'www.']:
            if display_url.startswith(prefix):
                display_url = display_url[len(prefix):]
        # Remove trailing slash and path
        display_url = display_url.rstrip('/').split('/')[0]
        
        addr_suffix = f" ({indexer_id[:6]}..)"
        max_url_len = max_width - len(addr_suffix)
        if len(display_url) > max_url_len:
            # Cut from beginning with ".." prefix
            display_url = ".." + display_url[-(max_url_len-2):]
        return f"{display_url}{addr_suffix}"
    
    return indexer_id[:10] + ".." if len(indexer_id) > 10 else indexer_id


def format_sync_status(status: Optional[Dict]) -> str:
    """Format sync status as a colored indicator (wrapper using local Colors)"""
    return _format_sync_status(status, Colors)


def main():
    parser = argparse.ArgumentParser(
        description='CLI tool to analyze TheGraph allocations and signals',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Configuration:
  TheGraph Network subgraph URL can be configured via:
    - Environment variable: THEGRAPH_NETWORK_SUBGRAPH_URL
    - Config file: ~/.grtinfo/config.json (key "network_subgraph_url")
    - Option --url (overrides all)

  Your indexer ID (for highlighting) can be configured via:
    - Environment variable: MY_INDEXER_ID
    - Config file: ~/.grtinfo/config.json (key "my_indexer_id")

Example:
  export THEGRAPH_NETWORK_SUBGRAPH_URL="https://your-graph-node/subgraphs/id/QmNetworkHash"
  export MY_INDEXER_ID="0xYourIndexerAddress"
  subinfo QmYourSubgraphHash
        """
    )
    parser.add_argument(
        'subgraph_hash',
        help='IPFS hash of the subgraph you want to see information for'
    )
    parser.add_argument(
        '--url',
        help='TheGraph Network subgraph URL (overrides config and environment variable)',
        default=None
    )
    parser.add_argument(
        '--hours',
        type=int,
        default=48,
        help='Number of hours for history (default: 48)'
    )
    parser.add_argument(
        '-v', '--verbose',
        action='count',
        default=0,
        help='Increase verbosity (use -v for info, -vv for debug)'
    )
    
    args = parser.parse_args()
    
    # Setup logging based on verbosity
    setup_logging(verbosity=args.verbose)
    
    # Get network subgraph URL
    network_url = args.url or get_network_subgraph_url()
    
    # Build complete network subgraph URL
    if not network_url.endswith('/'):
        network_url += '/'
    
    # Network URL should point to the network subgraph (without specific hash)
    # Expected format: http://host/subgraphs/id/{network_hash}
    # But we can also have just the base URL if the network is directly accessible
    # For now, we use the URL as is since it should point to the network subgraph
    
    # Create client to query network subgraph
    client = TheGraphClient(network_url)
    
    # Get subgraph ID for explorer link
    subgraph_id = client.get_subgraph_id(args.subgraph_hash)
    hash_link = format_deployment_link(args.subgraph_hash, subgraph_id)
    print(f"{Colors.BOLD}Subgraph:{Colors.RESET} {Colors.CYAN}{hash_link}{Colors.RESET}")
    
    # Verify it's the TheGraph Network subgraph
    try:
        if not client.is_network_subgraph():
            print("Error: The configured URL does not appear to point to the TheGraph Network subgraph.")
            print("The 'subinfo' tool requires the TheGraph Network subgraph URL")
            print("that contains information about allocations and curation signals.")
            print("\nConfigure the THEGRAPH_NETWORK_SUBGRAPH_URL environment variable")
            print("or modify ~/.grtinfo/config.json with 'network_subgraph_url'")
            sys.exit(1)
    except Exception as e:
        print(f"Error verifying network subgraph: {e}", file=sys.stderr)
        sys.exit(1)
    
    # Get user's indexer ID
    my_indexer_id = get_my_indexer_id()
    
    # Initialize ENS client if configured
    ens_client = None
    ens_url = get_ens_subgraph_url()
    if ens_url:
        try:
            ens_client = ENSClient(ens_url)
        except Exception as e:
            print(f"{Colors.DIM}Warning: Unable to initialize ENS client: {e}{Colors.RESET}\n", file=sys.stderr)
    
    # Create a shared executor for async operations
    sync_executor = ThreadPoolExecutor(max_workers=15)
    sync_context = None
    
    try:
        # 1. Subgraph metadata
        subgraph_metadata = client.get_subgraph_metadata(args.subgraph_hash)
        print_subgraph_metadata(subgraph_metadata)
        
        # 2. Curation signal
        curation_signal = client.get_curation_signal(args.subgraph_hash)
        print_curation_signal(curation_signal)
        
        # 3. Signal changes
        signal_changes = client.get_curation_signal_changes(args.subgraph_hash, args.hours)
        print_signal_changes(signal_changes, args.hours)
        
        # 4. Current allocations
        current_allocations = client.get_current_allocations(args.subgraph_hash)
        
        # 5. Allocation history (created in last N hours)
        allocation_history = client.get_allocation_history(args.subgraph_hash, args.hours)
        
        # 6. Unallocations (closed in last N hours)
        unallocations = client.get_unallocations(args.subgraph_hash, args.hours)
        
        # 7. Get POI submissions (reward collections for long-running allocations)
        poi_submissions = client.get_poi_submissions(args.subgraph_hash, args.hours)
        
        # 8. Collect all indexer IDs and fetch their URLs (fallback for ENS)
        all_indexer_ids = set()
        for alloc in current_allocations:
            all_indexer_ids.add(alloc.get('indexer', {}).get('id', ''))
        for alloc in allocation_history:
            all_indexer_ids.add(alloc.get('indexer', {}).get('id', ''))
        for unalloc in unallocations:
            all_indexer_ids.add(unalloc.get('indexer', {}).get('id', ''))
        for poi in poi_submissions:
            all_indexer_ids.add(poi.get('allocation', {}).get('indexer', {}).get('id', ''))
        all_indexer_ids.discard('')
        
        indexer_urls = client.get_indexers_urls(list(all_indexer_ids)) if all_indexer_ids else {}
        
        # 8b. Start async sync status fetching NOW (runs in background while we continue)
        sync_context = fetch_sync_statuses_async(indexer_urls, args.subgraph_hash, sync_executor)
        log.debug(f"Started async sync fetch for {len(indexer_urls)} indexers")
        
        # 9. Get stake info for indexers who unallocated (to detect high unallocated stake)
        unalloc_indexer_ids = [u.get('indexer', {}).get('id', '') for u in unallocations]
        indexers_stake_info = client.get_indexers_stake_info(unalloc_indexer_ids) if unalloc_indexer_ids else {}
        
        # 10. Collect sync statuses while we display other sections (most should be ready by now)
        # Give it a short timeout since we've already waited for metadata/curation/signal
        sync_statuses = {}
        sync_errors = {}
        if sync_context and sync_context.get('futures'):
            sync_statuses, sync_errors = collect_sync_statuses(sync_context, timeout=5.0)
        
        # 11. Display allocations with sync status inline (no placeholders, no cursor manipulation)
        reward_proportion = subgraph_metadata.get('rewardProportion') if subgraph_metadata else None
        _, _ = print_allocations(
            current_allocations, "Active Allocations", my_indexer_id, ens_client, 
            indexer_urls, reward_proportion, network_url, args.subgraph_hash,
            sync_statuses=sync_statuses, sync_errors=sync_errors
        )
        
        # 12. Combined allocations/unallocations/collections timeline
        print_allocations_timeline(allocation_history, unallocations, poi_submissions, args.hours, my_indexer_id, ens_client, indexers_stake_info, indexer_urls)
        
        print()  # Final newline
        
    except requests.exceptions.RequestException as e:
        print(f"Connection error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        # Clean up executor
        sync_executor.shutdown(wait=False)


if __name__ == '__main__':
    main()

