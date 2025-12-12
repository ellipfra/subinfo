#!/usr/bin/env python3
"""
CLI tool to analyze TheGraph allocations and curation signals

Usage:
    subinfo <subgraph_hash>

The subgraph_hash is the IPFS hash of the subgraph you want to see information for.
The TheGraph Network subgraph URL must be configured via:
    - Environment variable: THEGRAPH_NETWORK_SUBGRAPH_URL
    - Config file: ~/.subinfo/config.json (key "network_subgraph_url")

Example:
    export THEGRAPH_NETWORK_SUBGRAPH_URL="https://your-graph-node/subgraphs/id/QmNetworkSubgraphHash"
    subinfo QmYourSubgraphHash
"""

import sys
import json
import argparse
import os
import re
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import requests
from pathlib import Path

# ANSI color codes
class Colors:
    RESET = '\033[0m'
    BOLD = '\033[1m'
    
    # Base colors
    GREEN = '\033[32m'
    RED = '\033[31m'
    YELLOW = '\033[33m'
    BLUE = '\033[34m'
    MAGENTA = '\033[35m'
    CYAN = '\033[36m'
    WHITE = '\033[37m'
    
    # Bright colors
    BRIGHT_GREEN = '\033[92m'
    BRIGHT_RED = '\033[91m'
    BRIGHT_YELLOW = '\033[93m'
    BRIGHT_BLUE = '\033[94m'
    BRIGHT_MAGENTA = '\033[95m'
    BRIGHT_CYAN = '\033[96m'
    
    # Styles
    DIM = '\033[2m'
    
    @staticmethod
    def disable():
        """Disable colors (for file redirection)"""
        Colors.RESET = ''
        Colors.BOLD = ''
        Colors.GREEN = Colors.BRIGHT_GREEN = ''
        Colors.RED = Colors.BRIGHT_RED = ''
        Colors.YELLOW = Colors.BRIGHT_YELLOW = ''
        Colors.BLUE = Colors.BRIGHT_BLUE = ''
        Colors.MAGENTA = Colors.BRIGHT_MAGENTA = ''
        Colors.CYAN = Colors.BRIGHT_CYAN = ''
        Colors.WHITE = ''
        Colors.DIM = ''

# Disable colors if output is not a terminal
if not sys.stdout.isatty():
    Colors.disable()


class TheGraphClient:
    """Client to query TheGraph Network subgraph"""
    
    def __init__(self, subgraph_url: str):
        # The subgraph URL is directly the GraphQL endpoint
        self.subgraph_url = subgraph_url.rstrip('/')
        self._cache_file = Path.home() / '.subinfo' / 'network_totals_cache.json'
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
            response = requests.post(
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
            }}
        }}
        """
        try:
            result = self.query(query)
            return result.get('allocations', [])
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
                print(f"{Colors.DIM}Calculating network totals...{Colors.RESET}", file=sys.stderr, end='', flush=True)
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
                        print(f"{Colors.DIM}.{Colors.RESET}", file=sys.stderr, end='', flush=True)
                    
                    if len(deployments) < batch_size:
                        break
                    
                    skip += batch_size
                
                print(f"{Colors.DIM} done{Colors.RESET}\n", file=sys.stderr)
                
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
                signalAmount
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
                    signalAmount
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
            'signalAmount': deployment.get('signalAmount', '0'),
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
            if new_deployment_info is not None:
                # Add upgrade as signal change (only if not already added)
                upgrade_exists = any(c.get('type') == 'upgrade' and c.get('timestamp') == str(new_deployment_info['created']) for c in changes)
                if not upgrade_exists:
                    changes.insert(0, {  # Insert at beginning to show upgrade first
                        'type': 'upgrade',
                        'signaller': 'Subgraph Upgrade',
                        'tokens': new_deployment_info['signal'],
                        'timestamp': str(new_deployment_info['created']),
                        'new_deployment_hash': new_deployment_info['hash']
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
                    new_deployment = upgrade_result.get('newDeployment', {})
                    if new_deployment:
                        new_dep_created = int(new_deployment.get('createdAt', '0'))
                        new_signal = new_deployment.get('signalAmount', '0')
                        # Add upgrade as signal change (only if not already added)
                        upgrade_exists = any(c.get('type') == 'upgrade' and c.get('timestamp') == str(new_dep_created) for c in changes)
                        if not upgrade_exists:
                            changes.insert(0, {  # Insert at beginning to show upgrade first
                                'type': 'upgrade',
                                'signaller': 'Subgraph Upgrade',
                                'tokens': new_signal,
                                'timestamp': str(new_dep_created),
                                'new_deployment_hash': new_deployment.get('ipfsHash', 'Unknown')
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


class ENSClient:
    """Client to query ENS subgraph and resolve addresses to names"""
    
    def __init__(self, ens_subgraph_url: str):
        self.ens_subgraph_url = ens_subgraph_url.rstrip('/')
        self._cache = {}  # Cache to avoid repeated queries
    
    def query(self, query: str, variables: Optional[Dict] = None) -> Dict:
        """Execute a GraphQL query"""
        try:
            response = requests.post(
                self.ens_subgraph_url,
                json={'query': query, 'variables': variables or {}},
                headers={'Content-Type': 'application/json'},
                timeout=10
            )
            response.raise_for_status()
            data = response.json()
            if 'errors' in data:
                return {}
            return data.get('data', {})
        except:
            return {}
    
    def resolve_address(self, address: str) -> Optional[str]:
        """Resolve an Ethereum address to an ENS name"""
        if not address or address == 'Unknown':
            return None
        
        # Check cache
        address_lower = address.lower()
        if address_lower in self._cache:
            return self._cache[address_lower]
        
        # Query to find ENS name
        query = """
        query ResolveAddress($address: String!) {
            domains(
                where: { resolvedAddress: $address }
                first: 1
                orderBy: createdAt
                orderDirection: desc
            ) {
                name
            }
        }
        """
        
        try:
            result = self.query(query, {'address': address_lower})
            domains = result.get('domains', [])
            if domains:
                name = domains[0].get('name')
                if name:
                    self._cache[address_lower] = name
                    return name
        except:
            pass
        
        # If not found, put None in cache to avoid retrying
        self._cache[address_lower] = None
        return None
    
    def resolve_addresses_batch(self, addresses: List[str]) -> Dict[str, Optional[str]]:
        """Resolve multiple addresses in a single query"""
        results = {}
        addresses_lower = [addr.lower() for addr in addresses if addr and addr != 'Unknown']
        
        if not addresses_lower:
            return results
        
        # Batch query
        query = """
        query ResolveAddresses($addresses: [String!]!) {
            domains(
                where: { resolvedAddress_in: $addresses }
                first: 100
            ) {
                name
                resolvedAddress {
                    id
                }
            }
        }
        """
        
        try:
            result = self.query(query, {'addresses': addresses_lower})
            domains = result.get('domains', [])
            for domain in domains:
                addr = domain.get('resolvedAddress', {}).get('id', '').lower()
                name = domain.get('name')
                if addr and name:
                    results[addr] = name
                    self._cache[addr] = name
            
            # Put None for addresses not found
            for addr in addresses_lower:
                if addr not in results:
                    results[addr] = None
                    self._cache[addr] = None
        except:
            pass
        
        return results


def format_timestamp(ts: str) -> str:
    """Format a Unix timestamp to a readable date"""
    try:
        dt = datetime.fromtimestamp(int(ts))
        return dt.strftime('%Y-%m-%d %H:%M:%S')
    except:
        return ts


def format_tokens(tokens: str) -> str:
    """Format tokens to a readable format (no decimals unless < 1 GRT)"""
    try:
        amount = float(tokens) / 1e18  # GRT has 18 decimals
        if amount < 1:
            return f"{amount:,.2f} GRT"
        return f"{amount:,.0f} GRT"
    except:
        return tokens


def format_duration(seconds: float) -> str:
    """Format duration in seconds to a human-readable format"""
    if seconds < 60:
        return f"{int(seconds)}s"
    elif seconds < 3600:
        minutes = int(seconds / 60)
        return f"{minutes}m"
    elif seconds < 86400:
        hours = int(seconds / 3600)
        minutes = int((seconds % 3600) / 60)
        if minutes > 0:
            return f"{hours}h {minutes}m"
        return f"{hours}h"
    else:
        days = int(seconds / 86400)
        hours = int((seconds % 86400) / 3600)
        if hours > 0:
            return f"{days}d {hours}h"
        return f"{days}d"


def print_section(title: str):
    """Display a compact section title with color"""
    print(f"\n{Colors.CYAN}{Colors.BOLD}{title}{Colors.RESET}")
    print(f"{Colors.DIM}{'-' * 60}{Colors.RESET}")


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
    
    print()


def print_allocations(allocations: List[Dict], title: str, my_indexer_id: Optional[str] = None, ens_client: Optional[ENSClient] = None, indexer_urls: Optional[Dict[str, str]] = None):
    """Display allocations in a compact format with colors"""
    print_section(title)
    if not allocations:
        print(f"{Colors.DIM}No allocations found.{Colors.RESET}\n")
        return
    
    # Resolve ENS names in batch
    indexer_addresses = [alloc.get('indexer', {}).get('id', '') for alloc in allocations]
    ens_names = {}
    if ens_client:
        ens_names = ens_client.resolve_addresses_batch(indexer_addresses)
    
    total = 0
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
        
        # Calculate padding accounting for ANSI codes
        marker_width = get_display_width(marker)
        indexer_display_width = get_display_width(indexer_display)
        tokens_str_width = get_display_width(tokens_str)
        
        # Target widths: marker=1, indexer=37, tokens=18, date=16
        marker_padding = max(0, 1 - marker_width)
        indexer_padding = max(0, 37 - indexer_display_width)
        tokens_padding = max(0, 18 - tokens_str_width)
        
        if alloc.get('closedAt'):
            closed = format_timestamp(str(alloc.get('closedAt', '0')))[:16]
            print(f"  {marker}{' ' * marker_padding}  {indexer_color}{indexer_display}{' ' * indexer_padding}{Colors.RESET}  {Colors.BRIGHT_GREEN}{' ' * tokens_padding}{tokens_str}{Colors.RESET}  {Colors.DIM}{created}{Colors.RESET}  {status_color}{status}{Colors.RESET}")
        else:
            print(f"  {marker}{' ' * marker_padding}  {indexer_color}{indexer_display}{' ' * indexer_padding}{Colors.RESET}  {Colors.BRIGHT_GREEN}{' ' * tokens_padding}{tokens_str}{Colors.RESET}  {Colors.DIM}{created}{Colors.RESET}  {status_color}{status}{Colors.RESET}{Colors.DIM}{duration_str}{Colors.RESET}")
    
    total_fmt = f"{total:,.2f}" if total < 1 else f"{total:,.0f}"
    print(f"\n{Colors.BOLD}Total: {Colors.BRIGHT_GREEN}{total_fmt} GRT{Colors.RESET}\n")


def print_allocations_timeline(allocations: List[Dict], unallocations: List[Dict], hours: int = 48, my_indexer_id: Optional[str] = None, ens_client: Optional[ENSClient] = None, indexers_stake_info: Optional[Dict] = None, indexer_urls: Optional[Dict[str, str]] = None):
    """Display allocations and unallocations in a chronological timeline with colors"""
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
            'createdAt': unalloc.get('createdAt', '0')
        })
    
    if not events:
        print(f"{Colors.DIM}No events found.{Colors.RESET}\n")
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
        else:  # unallocation
            total_unallocated += amount
            symbol = f"{Colors.BRIGHT_RED}-{Colors.RESET}"
            status_color = Colors.BRIGHT_RED
        
        # Calculate padding accounting for ANSI codes
        symbol_width = get_display_width(symbol)
        marker_width = get_display_width(marker)
        indexer_display_width = get_display_width(indexer_display)
        tokens_str_width = get_display_width(tokens_str)
        
        # Target widths: symbol=1, marker=1, indexer=37, tokens=18, date=16
        symbol_padding = max(0, 1 - symbol_width)
        marker_padding = max(0, 1 - marker_width)
        indexer_padding = max(0, 37 - indexer_display_width)
        tokens_padding = max(0, 18 - tokens_str_width)
        
        if event['type'] == 'allocation':
            if event.get('closedAt'):
                closed = format_timestamp(str(event['closedAt']))[:16]
                print(f"  [{symbol}]{' ' * symbol_padding} {marker}{' ' * marker_padding}  {Colors.DIM}{timestamp}{Colors.RESET}  {indexer_color}{indexer_display}{' ' * indexer_padding}{Colors.RESET}  {Colors.BRIGHT_GREEN}{' ' * tokens_padding}{tokens_str}{Colors.RESET}  {status_color}{status}{Colors.RESET} → closed {Colors.DIM}{closed}{Colors.RESET}")
            else:
                print(f"  [{symbol}]{' ' * symbol_padding} {marker}{' ' * marker_padding}  {Colors.DIM}{timestamp}{Colors.RESET}  {indexer_color}{indexer_display}{' ' * indexer_padding}{Colors.RESET}  {Colors.BRIGHT_GREEN}{' ' * tokens_padding}{tokens_str}{Colors.RESET}  {status_color}{status}{Colors.RESET}")
        else:  # unallocation
            created = format_timestamp(str(event.get('createdAt', '0')))[:16]
            # Check if indexer has high unallocated stake
            warning = ""
            if indexers_stake_info:
                stake_info = indexers_stake_info.get(indexer_id.lower())
                if stake_info and stake_info.get('unallocated_pct', 0) > 30:
                    unalloc_pct = stake_info['unallocated_pct']
                    warning = f" {Colors.BRIGHT_YELLOW}⚠ {unalloc_pct:.0f}% unallocated{Colors.RESET}"
            print(f"  [{symbol}]{' ' * symbol_padding} {marker}{' ' * marker_padding}  {Colors.DIM}{timestamp}{Colors.RESET}  {indexer_color}{indexer_display}{' ' * indexer_padding}{Colors.RESET}  {Colors.BRIGHT_RED}{' ' * tokens_padding}{tokens_str}{Colors.RESET}  closed{warning}")
    
    alloc_fmt = f"{total_allocated:,.2f}" if total_allocated < 1 else f"{total_allocated:,.0f}"
    unalloc_fmt = f"{total_unallocated:,.2f}" if total_unallocated < 1 else f"{total_unallocated:,.0f}"
    print(f"\n{Colors.BOLD}Total allocated: {Colors.BRIGHT_GREEN}{alloc_fmt} GRT{Colors.RESET} | Total unallocated: {Colors.BRIGHT_RED}{unalloc_fmt} GRT{Colors.RESET}\n")


def print_curation_signal(signal_data: Optional[Dict]):
    """Display curation signal in a compact format with colors"""
    print_section("Curation Signal")
    if not signal_data:
        print(f"{Colors.DIM}No curation signal found.{Colors.RESET}\n")
        return
    
    signal_amount = format_tokens(signal_data.get('signalAmount', '0'))
    print(f"{Colors.BOLD}Total signal:{Colors.RESET} {Colors.BRIGHT_CYAN}{signal_amount}{Colors.RESET}")
    
    signals = signal_data.get('signals', [])
    if signals:
        print(f"{Colors.DIM}Signals: {len(signals)}{Colors.RESET}")
    print()


def print_signal_changes(changes: List[Dict], hours: int = 48, new_deployment_info: Optional[Dict] = None):
    """Display signal changes in a compact format with colors"""
    print_section(f"Signal Changes ({hours}h)")
    
    # Add new deployment event if applicable
    display_changes = list(changes)
    if new_deployment_info and new_deployment_info.get('isNewDeployment'):
        created_at = new_deployment_info.get('deploymentCreatedAt')
        signal_amount = new_deployment_info.get('signalAmount', '0')
        if created_at:
            display_changes.append({
                'type': 'new_deployment',
                'timestamp': created_at,
                'tokens': signal_amount
            })
            # Sort by timestamp descending
            display_changes.sort(key=lambda x: int(x.get('timestamp', '0')), reverse=True)
    
    if not display_changes:
        print(f"{Colors.DIM}No changes found.{Colors.RESET}\n")
        return
    
    total_added = 0
    total_removed = 0
    
    for change in display_changes:
        change_type = change.get('type', 'unknown')
        tokens = change.get('tokens', '0')
        amount = float(tokens) / 1e18
        signaller = change.get('signaller', 'Unknown')
        signaller_short = signaller[:10] + "..." if len(signaller) > 10 else signaller
        timestamp = format_timestamp(str(change.get('timestamp', '0')))[:16]
        
        if change_type == 'new_deployment':
            # New deployment indicator
            symbol = f"{Colors.BRIGHT_YELLOW}★{Colors.RESET}"
            token_color = Colors.BRIGHT_YELLOW
            signaller_display = f"{Colors.BRIGHT_YELLOW}New deployment created{Colors.RESET}"
            tokens_str = format_tokens(tokens)
            print(f"  [{symbol}]  {Colors.DIM}{timestamp:16}{Colors.RESET}  {signaller_display:44}  {token_color}{tokens_str:>18}{Colors.RESET}")
            continue
        elif change_type == 'upgrade':
            # Special handling for upgrades
            total_added += amount
            symbol = f"{Colors.BRIGHT_YELLOW}↑{Colors.RESET}"
            token_color = Colors.BRIGHT_YELLOW
            new_hash = change.get('new_deployment_hash', 'Unknown')
            signaller_display = f"{Colors.BRIGHT_YELLOW}Upgrade → {new_hash}{Colors.RESET}"
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
        print(f"  [{symbol}]  {Colors.DIM}{timestamp:16}{Colors.RESET}  {signaller_display if change_type == 'upgrade' else Colors.WHITE + signaller_display + Colors.RESET:35}  {token_color}{tokens_str:>18}{Colors.RESET}")
    
    net = total_added - total_removed
    net_color = Colors.BRIGHT_GREEN if net >= 0 else Colors.BRIGHT_RED
    added_fmt = f"{total_added:,.2f}" if total_added < 1 else f"{total_added:,.0f}"
    removed_fmt = f"{total_removed:,.2f}" if total_removed < 1 else f"{total_removed:,.0f}"
    net_fmt = f"{net:,.2f}" if abs(net) < 1 else f"{net:,.0f}"
    print(f"\n{Colors.BOLD}Total added: {Colors.BRIGHT_GREEN}{added_fmt} GRT{Colors.RESET} | Removed: {Colors.BRIGHT_RED}{removed_fmt} GRT{Colors.RESET} | Net: {net_color}{net_fmt} GRT{Colors.RESET}\n")


def get_network_subgraph_url() -> str:
    """Get network subgraph URL from environment variable or config"""
    # Priority 1: Environment variable
    env_url = os.environ.get('THEGRAPH_NETWORK_SUBGRAPH_URL')
    if env_url:
        return env_url.rstrip('/')
    
    # Priority 2: Config file
    config_file = Path.home() / '.subinfo' / 'config.json'
    if config_file.exists():
        try:
            with open(config_file, 'r') as f:
                content = f.read().strip()
                if not content:
                    print(f"Warning: Config file {config_file} is empty", file=sys.stderr)
                else:
                    config = json.loads(content)
                    url = config.get('network_subgraph_url') or config.get('subgraph_url')
                    if url:
                        return url.rstrip('/')
        except json.JSONDecodeError as e:
            print(f"Error: Config file {config_file} is not valid JSON.", file=sys.stderr)
            print(f"  Error: {e}", file=sys.stderr)
            print(f"  File must be in JSON format, example:", file=sys.stderr)
            print(f'  {{"network_subgraph_url": "http://host/subgraphs/id/QmHash"}}', file=sys.stderr)
        except Exception as e:
            print(f"Warning: Unable to load config: {e}", file=sys.stderr)
    
    # Priority 3: No default - must be configured
    print("Error: No TheGraph Network subgraph URL configured.", file=sys.stderr)
    print("Please set THEGRAPH_NETWORK_SUBGRAPH_URL environment variable", file=sys.stderr)
    print("or create ~/.subinfo/config.json with 'network_subgraph_url' key.", file=sys.stderr)
    sys.exit(1)


def get_my_indexer_id() -> Optional[str]:
    """Get user's indexer ID from environment variable or config"""
    # Priority 1: Environment variable
    env_indexer = os.environ.get('MY_INDEXER_ID')
    if env_indexer:
        return env_indexer.lower()
    
    # Priority 2: Config file
    config_file = Path.home() / '.subinfo' / 'config.json'
    if config_file.exists():
        try:
            with open(config_file, 'r') as f:
                content = f.read().strip()
                if content:
                    config = json.loads(content)
                    indexer_id = config.get('my_indexer_id')
                    if indexer_id:
                        return indexer_id.lower()
        except:
            pass
    
    # Priority 3: No default - indexer highlighting is optional
    return None


def get_ens_subgraph_url() -> Optional[str]:
    """Get ENS subgraph URL from environment variable or config"""
    # Priority 1: Environment variable
    env_url = os.environ.get('ENS_SUBGRAPH_URL')
    if env_url:
        return env_url.rstrip('/')
    
    # Priority 2: Config file
    config_file = Path.home() / '.subinfo' / 'config.json'
    if config_file.exists():
        try:
            with open(config_file, 'r') as f:
                content = f.read().strip()
                if content:
                    config = json.loads(content)
                    url = config.get('ens_subgraph_url')
                    if url:
                        return url.rstrip('/')
        except:
            pass
    
    # Priority 3: Default value (hash provided by user)
    # Build URL with configured network
    network_url = get_network_subgraph_url()
    # Extract base URL (without hash)
    if '/subgraphs/id/' in network_url:
        base_url = network_url.split('/subgraphs/id/')[0] + '/subgraphs/id'
        return f"{base_url}/QmcE8RpWtsiN5hkJKdfCXGfTDoTgPEjMbQwnjLPfThT7kZ"
    
    return None


def format_indexer_display(indexer_id: str, ens_name: Optional[str] = None, url: Optional[str] = None, max_width: int = 37) -> str:
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


def strip_ansi(text: str) -> str:
    """Remove ANSI color codes from text"""
    ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    return ansi_escape.sub('', text)


def get_display_width(text: str) -> int:
    """Get display width of text without ANSI codes"""
    return len(strip_ansi(text))


def main():
    parser = argparse.ArgumentParser(
        description='CLI tool to analyze TheGraph allocations and signals',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Configuration:
  TheGraph Network subgraph URL can be configured via:
    - Environment variable: THEGRAPH_NETWORK_SUBGRAPH_URL
    - Config file: ~/.subinfo/config.json (key "network_subgraph_url")
    - Option --url (overrides all)

  Your indexer ID (for highlighting) can be configured via:
    - Environment variable: MY_INDEXER_ID
    - Config file: ~/.subinfo/config.json (key "my_indexer_id")

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
    
    args = parser.parse_args()
    
    # Get network subgraph URL
    network_url = args.url or get_network_subgraph_url()
    
    # Build complete network subgraph URL
    if not network_url.endswith('/'):
        network_url += '/'
    
    # Network URL should point to the network subgraph (without specific hash)
    # Expected format: http://host/subgraphs/id/{network_hash}
    # But we can also have just the base URL if the network is directly accessible
    # For now, we use the URL as is since it should point to the network subgraph
    
    print(f"{Colors.BOLD}Subgraph:{Colors.RESET} {Colors.CYAN}{args.subgraph_hash}{Colors.RESET}\n")
    
    # Create client to query network subgraph
    client = TheGraphClient(network_url)
    
    # Verify it's the TheGraph Network subgraph
    try:
        if not client.is_network_subgraph():
            print("Error: The configured URL does not appear to point to the TheGraph Network subgraph.")
            print("The 'subinfo' tool requires the TheGraph Network subgraph URL")
            print("that contains information about allocations and curation signals.")
            print("\nConfigure the THEGRAPH_NETWORK_SUBGRAPH_URL environment variable")
            print("or modify ~/.subinfo/config.json with 'network_subgraph_url'")
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
    
    try:
        # 1. Subgraph metadata
        subgraph_metadata = client.get_subgraph_metadata(args.subgraph_hash)
        print_subgraph_metadata(subgraph_metadata)
        
        # 2. Curation signal
        curation_signal = client.get_curation_signal(args.subgraph_hash)
        print_curation_signal(curation_signal)
        
        # 3. Signal changes (pass new deployment info for display in history)
        signal_changes = client.get_curation_signal_changes(args.subgraph_hash, args.hours)
        print_signal_changes(signal_changes, args.hours, curation_signal)
        
        # 4. Current allocations
        current_allocations = client.get_current_allocations(args.subgraph_hash)
        
        # 5. Allocation history (created in last N hours)
        allocation_history = client.get_allocation_history(args.subgraph_hash, args.hours)
        
        # 6. Unallocations (closed in last N hours)
        unallocations = client.get_unallocations(args.subgraph_hash, args.hours)
        
        # 7. Collect all indexer IDs and fetch their URLs (fallback for ENS)
        all_indexer_ids = set()
        for alloc in current_allocations:
            all_indexer_ids.add(alloc.get('indexer', {}).get('id', ''))
        for alloc in allocation_history:
            all_indexer_ids.add(alloc.get('indexer', {}).get('id', ''))
        for unalloc in unallocations:
            all_indexer_ids.add(unalloc.get('indexer', {}).get('id', ''))
        all_indexer_ids.discard('')
        
        indexer_urls = client.get_indexers_urls(list(all_indexer_ids)) if all_indexer_ids else {}
        
        # 8. Get stake info for indexers who unallocated (to detect high unallocated stake)
        unalloc_indexer_ids = [u.get('indexer', {}).get('id', '') for u in unallocations]
        indexers_stake_info = client.get_indexers_stake_info(unalloc_indexer_ids) if unalloc_indexer_ids else {}
        
        # 9. Display allocations
        print_allocations(current_allocations, "Active Allocations", my_indexer_id, ens_client, indexer_urls)
        
        # 10. Combined allocations/unallocations timeline
        print_allocations_timeline(allocation_history, unallocations, args.hours, my_indexer_id, ens_client, indexers_stake_info, indexer_urls)
        
    except requests.exceptions.RequestException as e:
        print(f"Connection error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()

