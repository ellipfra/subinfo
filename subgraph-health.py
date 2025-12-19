#!/usr/bin/env python3
"""
Subgraph Health Monitor - Monitor health of subgraphs with active allocations
"""

import argparse
import json
import os
import requests
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ANSI Colors
class Colors:
    RESET = '\033[0m'
    BOLD = '\033[1m'
    DIM = '\033[2m'
    RED = '\033[91m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'


def terminal_link(url: str, text: str) -> str:
    """Create a clickable terminal hyperlink (OSC 8)"""
    return f'\033]8;;{url}\033\\{text}\033]8;;\033\\'


def format_deployment_link(ipfs_hash: str, subgraph_id: str = None) -> str:
    """Format IPFS hash as a clickable link to The Graph Explorer"""
    if subgraph_id:
        url = f"https://thegraph.com/explorer/subgraphs/{subgraph_id}?view=Query&chain=arbitrum-one"
        return terminal_link(url, ipfs_hash)
    return ipfs_hash


class Cache:
    """Simple file-based cache with TTL"""
    
    def __init__(self, cache_dir: Path, ttl_seconds: int = 300):
        self.cache_dir = cache_dir
        self.ttl_seconds = ttl_seconds
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._memory_cache = {}
    
    def get(self, key: str) -> Optional[dict]:
        # Check memory cache first
        if key in self._memory_cache:
            entry = self._memory_cache[key]
            if time.time() < entry['expires']:
                return entry['data']
        
        # Check file cache
        cache_file = self.cache_dir / f"{key}.json"
        if cache_file.exists():
            try:
                with open(cache_file, 'r') as f:
                    entry = json.load(f)
                if time.time() < entry.get('expires', 0):
                    self._memory_cache[key] = entry
                    return entry['data']
            except:
                pass
        return None
    
    def set(self, key: str, data: dict):
        entry = {
            'data': data,
            'expires': time.time() + self.ttl_seconds
        }
        self._memory_cache[key] = entry
        cache_file = self.cache_dir / f"{key}.json"
        try:
            with open(cache_file, 'w') as f:
                json.dump(entry, f)
        except:
            pass


class HistoryManager:
    """Manage history of issues for tracking regressions/corrections"""
    
    def __init__(self, config_dir: Path):
        self.history_file = config_dir / 'history.json'
        self._data = self._load()
    
    def _load(self) -> Dict:
        if self.history_file.exists():
            try:
                with open(self.history_file, 'r') as f:
                    return json.load(f)
            except:
                pass
        return {'issues': {}, 'runs': [], 'last_run': None}
    
    def _save(self):
        with open(self.history_file, 'w') as f:
            json.dump(self._data, f, indent=2)
    
    def get_issue_duration(self, ipfs_hash: str) -> Optional[str]:
        """Get how long an issue has been present"""
        issue = self._data.get('issues', {}).get(ipfs_hash)
        if issue and issue.get('first_seen') and issue.get('resolved_at') is None:
            return self._calculate_duration(issue['first_seen'])
        return None
    
    def _calculate_duration(self, first_seen_str: str) -> str:
        """Calculate human-readable duration from first_seen to now"""
        try:
            first_seen = datetime.fromisoformat(first_seen_str)
            delta = datetime.now() - first_seen
            
            if delta.total_seconds() < 60:
                return "just now"
            elif delta.total_seconds() < 3600:
                mins = int(delta.total_seconds() / 60)
                return f"{mins}m"
            elif delta.total_seconds() < 86400:
                hours = int(delta.total_seconds() / 3600)
                return f"{hours}h"
            else:
                days = int(delta.total_seconds() / 86400)
                return f"{days}d"
        except:
            return "?"
    
    def record_run(self, results: List[Dict]) -> Dict:
        """Record current run and return changes since last run"""
        now = datetime.now()
        now_str = now.isoformat()
        current_issues = {}
        
        # Extract current issues from results
        for result in results:
            info = result.get('info', {})
            issues = result.get('issues', [])
            warnings = result.get('warnings', [])
            
            if not issues and not warnings:
                continue  # Healthy
            
            ipfs_hash = info.get('ipfsHash', '')
            if not ipfs_hash:
                continue
            
            # Determine issue type
            issue_types = [i.get('type') for i in issues]
            if 'error' in issue_types:
                issue_type = 'error'
                # Check if it's our issue or subgraph issue
                if info.get('othersHealthy') == True:
                    issue_type = 'error_our_issue'
                elif info.get('othersHealthy') == False:
                    issue_type = 'error_subgraph_issue'
            elif 'sync_too_slow' in issue_types:
                issue_type = 'sync_too_slow'
            elif 'gap_growing' in issue_types:
                issue_type = 'gap_growing'
            elif 'No Prometheus metrics found' in warnings:
                issue_type = 'no_metrics'
            else:
                issue_type = 'other'
            
            current_issues[ipfs_hash] = {
                'type': issue_type,
                'network': info.get('network', ''),
                'allocated': info.get('allocatedTokens', 0)
            }
        
        new_issues = []
        resolved_issues = []
        
        # Get previous active issues (not resolved)
        previous_active = {h: d for h, d in self._data.get('issues', {}).items() 
                         if d.get('resolved_at') is None}
        
        # Find NEW issues (not in previous active issues)
        for ipfs_hash, issue_info in current_issues.items():
            existing = previous_active.get(ipfs_hash)
            
            if existing:
                # Issue already tracked - update last_seen and type
                self._data['issues'][ipfs_hash]['last_seen'] = now_str
                self._data['issues'][ipfs_hash]['issue_type'] = issue_info['type']
            else:
                # Truly new issue (or was resolved and came back)
                new_issues.append({
                    'ipfsHash': ipfs_hash,
                    'type': issue_info['type'],
                    'network': issue_info['network'],
                    'allocated': issue_info['allocated']
                })
                # Record in history (or update if it was resolved before)
                if ipfs_hash in self._data.get('issues', {}):
                    # Issue came back - update first_seen to now
                    self._data['issues'][ipfs_hash]['first_seen'] = now_str
                    self._data['issues'][ipfs_hash]['resolved_at'] = None
                else:
                    self._data['issues'][ipfs_hash] = {
                        'first_seen': now_str,
                        'last_seen': now_str,
                        'issue_type': issue_info['type'],
                        'network': issue_info['network'],
                        'resolved_at': None
                    }
                self._data['issues'][ipfs_hash]['last_seen'] = now_str
                self._data['issues'][ipfs_hash]['issue_type'] = issue_info['type']
        
        # Find RESOLVED issues (were in previous active, not in current)
        for ipfs_hash, issue_data in previous_active.items():
            if ipfs_hash not in current_issues:
                # Issue was resolved since last run
                self._data['issues'][ipfs_hash]['resolved_at'] = now_str
                resolved_issues.append({
                    'ipfsHash': ipfs_hash,
                    'type': issue_data.get('issue_type', 'unknown'),
                    'network': issue_data.get('network', ''),
                    'duration': self._calculate_duration(issue_data.get('first_seen'))
                })
        
        # Record run
        self._data['runs'].append(now_str)
        if len(self._data['runs']) > 100:
            self._data['runs'] = self._data['runs'][-100:]  # Keep last 100 runs
        
        self._data['last_run'] = {
            'timestamp': now_str,
            'total_issues': len(current_issues),
            'new_count': len(new_issues),
            'resolved_count': len(resolved_issues)
        }
        
        self._save()
        
        return {
            'new': new_issues,
            'resolved': resolved_issues
        }


class AcknowledgementManager:
    """Manage acknowledged issues"""
    
    CATEGORIES = {
        'ignore': {'days': None, 'description': 'Permanently ignored'},
        'wip': {'days': 7, 'description': 'Work in progress'},
        'external': {'days': 30, 'description': 'External/upstream issue'}
    }
    
    def __init__(self, config_dir: Path):
        self.ack_file = config_dir / 'acknowledged.json'
        self._data = self._load()
    
    def _load(self) -> Dict:
        if self.ack_file.exists():
            try:
                with open(self.ack_file, 'r') as f:
                    return json.load(f)
            except:
                pass
        return {}
    
    def _save(self):
        with open(self.ack_file, 'w') as f:
            json.dump(self._data, f, indent=2)
    
    def acknowledge(self, ipfs_hash: str, reason: str = "", category: str = "wip", expires: str = None) -> bool:
        """Acknowledge an issue"""
        if category not in self.CATEGORIES:
            print(f"Invalid category. Must be one of: {', '.join(self.CATEGORIES.keys())}")
            return False
        
        # Calculate expiry
        if expires:
            try:
                expires_dt = datetime.fromisoformat(expires)
            except:
                print(f"Invalid date format: {expires}")
                return False
        elif self.CATEGORIES[category]['days']:
            expires_dt = datetime.now() + timedelta(days=self.CATEGORIES[category]['days'])
        else:
            expires_dt = None
        
        self._data[ipfs_hash] = {
            'reason': reason,
            'category': category,
            'acknowledged_at': datetime.now().isoformat(),
            'expires': expires_dt.isoformat() if expires_dt else None
        }
        self._save()
        return True
    
    def unacknowledge(self, ipfs_hash: str) -> bool:
        """Remove acknowledgement for an issue"""
        if ipfs_hash in self._data:
            del self._data[ipfs_hash]
            self._save()
            return True
        return False
    
    def is_acknowledged(self, ipfs_hash: str) -> Optional[Dict]:
        """Check if an issue is acknowledged and not expired"""
        if ipfs_hash not in self._data:
            return None
        
        ack = self._data[ipfs_hash]
        if ack.get('expires'):
            try:
                expires = datetime.fromisoformat(ack['expires'])
                if datetime.now() > expires:
                    # Expired - remove it
                    del self._data[ipfs_hash]
                    self._save()
                    return None
            except:
                pass
        
        return ack
    
    def list_all(self) -> Dict:
        """List all acknowledgements"""
        # Clean expired ones first
        to_remove = []
        for ipfs_hash, ack in self._data.items():
            if ack.get('expires'):
                try:
                    expires = datetime.fromisoformat(ack['expires'])
                    if datetime.now() > expires:
                        to_remove.append(ipfs_hash)
                except:
                    pass
        
        for h in to_remove:
            del self._data[h]
        
        if to_remove:
            self._save()
        
        return self._data


class PrometheusClient:
    """Client for querying Prometheus metrics"""
    
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip('/')
    
    def query(self, query: str) -> Optional[List[Dict]]:
        """Execute a PromQL query"""
        try:
            response = requests.get(
                f"{self.base_url}/api/v1/query",
                params={'query': query},
                timeout=30
            )
            response.raise_for_status()
            data = response.json()
            if data.get('status') == 'success':
                return data.get('data', {}).get('result', [])
        except Exception as e:
            pass
        return None
    
    def get_deployment_head(self, deployment_id: str) -> Optional[int]:
        """Get the current head block for a deployment"""
        results = self.query(f'deployment_head{{deployment="{deployment_id}"}}')
        if results and len(results) > 0:
            try:
                return int(float(results[0]['value'][1]))
            except:
                pass
        return None
    
    def get_chain_head(self, network: str) -> Optional[int]:
        """Get the current chain head for a network"""
        results = self.query(f'ethereum_chain_head_number{{network="{network}"}}')
        if results and len(results) > 0:
            try:
                return int(float(results[0]['value'][1]))
            except:
                pass
        return None
    
    def get_deployment_status(self, deployment_id: str) -> Optional[str]:
        """Get deployment status (synced, syncing, failed)"""
        results = self.query(f'deployment_status{{deployment="{deployment_id}"}}')
        if results and len(results) > 0:
            return results[0].get('metric', {}).get('status')
        return None
    
    def get_blocks_per_hour(self, deployment_id: str) -> Optional[float]:
        """Get blocks processed per hour for a deployment"""
        # Get blocks processed count
        results = self.query(f'increase(deployment_blocks_processed_count{{deployment="{deployment_id}"}}[1h])')
        if results and len(results) > 0:
            try:
                return float(results[0]['value'][1])
            except:
                pass
        return None
    
    # Status codes from graph-node metrics
    STATUS_CODES = {
        1: 'unknown',
        2: 'synced',
        3: 'failed',
        4: 'syncing'
    }
    
    def get_all_deployment_metrics(self) -> Dict[str, Dict]:
        """Get metrics for all deployments at once"""
        metrics = {}
        
        # Get all deployment heads
        heads = self.query('deployment_head')
        if heads:
            for r in heads:
                dep = r.get('metric', {}).get('deployment', '')
                if dep:
                    if dep not in metrics:
                        metrics[dep] = {}
                    try:
                        metrics[dep]['head'] = int(float(r['value'][1]))
                    except:
                        pass
        
        # Get all deployment statuses (value is numeric: 1=unknown, 2=synced, 3=failed, 4=syncing)
        statuses = self.query('deployment_status')
        if statuses:
            for r in statuses:
                dep = r.get('metric', {}).get('deployment', '')
                if dep:
                    if dep not in metrics:
                        metrics[dep] = {}
                    try:
                        status_code = int(float(r['value'][1]))
                        metrics[dep]['status'] = self.STATUS_CODES.get(status_code, 'unknown')
                    except:
                        pass
        
        return metrics
    
    def get_all_chain_heads(self) -> Dict[str, int]:
        """Get chain head for all networks at once"""
        chain_heads = {}
        results = self.query('ethereum_chain_head_number')
        if results:
            for r in results:
                network = r.get('metric', {}).get('network', '')
                if network:
                    try:
                        chain_heads[network] = int(float(r['value'][1]))
                    except:
                        pass
        return chain_heads


class NetworkSubgraphClient:
    """Client for querying The Graph Network subgraph"""
    
    def __init__(self, url: str):
        self.url = url
    
    def query(self, query: str, variables: dict = None) -> Optional[Dict]:
        """Execute a GraphQL query"""
        try:
            response = requests.post(
                self.url,
                json={'query': query, 'variables': variables or {}},
                timeout=30
            )
            response.raise_for_status()
            return response.json().get('data')
        except Exception as e:
            return None
    
    def get_indexer_allocations(self, indexer_id: str) -> List[Dict]:
        """Get all active allocations for an indexer"""
        allocations = []
        skip = 0
        batch_size = 1000
        
        while True:
            query = """
            query($indexer: String!, $skip: Int!, $first: Int!) {
                allocations(
                    where: {indexer: $indexer, status: Active}
                    first: $first
                    skip: $skip
                ) {
                    id
                    allocatedTokens
                    createdAt
                    subgraphDeployment {
                        ipfsHash
                        manifest {
                            network
                        }
                        versions(first: 1, orderBy: createdAt, orderDirection: desc) {
                            subgraph {
                                id
                            }
                        }
                    }
                }
            }
            """
            
            data = self.query(query, {
                'indexer': indexer_id.lower(),
                'skip': skip,
                'first': batch_size
            })
            
            if not data or not data.get('allocations'):
                break
            
            batch = data['allocations']
            allocations.extend(batch)
            
            if len(batch) < batch_size:
                break
            
            skip += batch_size
        
        return allocations
    
    def get_other_indexers_for_deployment(self, deployment_ipfs: str, exclude_indexer: str) -> List[Dict]:
        """Get other indexers with allocations on a deployment"""
        query = """
        query($deployment: String!, $excludeIndexer: String!) {
            allocations(
                where: {
                    subgraphDeployment_: {ipfsHash: $deployment}
                    status: Active
                    indexer_not: $excludeIndexer
                }
                first: 100
            ) {
                indexer {
                    id
                    url
                }
            }
        }
        """
        
        data = self.query(query, {
            'deployment': deployment_ipfs,
            'excludeIndexer': exclude_indexer.lower()
        })
        
        if not data or not data.get('allocations'):
            return []
        
        # Deduplicate by indexer
        seen = set()
        indexers = []
        for alloc in data['allocations']:
            indexer = alloc.get('indexer', {})
            indexer_id = indexer.get('id')
            if indexer_id and indexer_id not in seen:
                seen.add(indexer_id)
                indexers.append(indexer)
        
        return indexers


def get_config() -> Dict:
    """Load configuration from file or environment"""
    config_dir = Path.home() / '.subgraph-health'
    config_file = config_dir / 'config.json'
    
    config = {
        'prometheus_url': os.environ.get('PROMETHEUS_URL', 'http://localhost:9090'),
        'network_subgraph_url': os.environ.get('NETWORK_SUBGRAPH_URL', 
            'https://api.thegraph.com/subgraphs/name/graphprotocol/graph-network-arbitrum'),
        'indexer_id': os.environ.get('INDEXER_ID', ''),
        'allocation_max_days': int(os.environ.get('ALLOCATION_MAX_DAYS', '28')),
        'chain_blocks_per_hour': {
            'mainnet': 300,
            'matic': 1800,
            'arbitrum-one': 15000,
            'base': 1800,
            'bsc': 1200,
            'avalanche': 1800,
            'gnosis': 720,
            'optimism': 1800,
            'celo': 720,
            'fantom': 3600,
            'moonbeam': 500,
            'moonriver': 500,
            'polygon-zkevm': 300,
            'zksync-era': 720,
            'linea': 300,
            'scroll': 300,
            'sonic': 1800,
            'arbitrum-sepolia': 15000
        }
    }
    
    if config_file.exists():
        try:
            with open(config_file, 'r') as f:
                file_config = json.load(f)
                config.update(file_config)
                # Support both 'indexer_id' and 'my_indexer_id' keys
                if 'my_indexer_id' in file_config and not config.get('indexer_id'):
                    config['indexer_id'] = file_config['my_indexer_id']
        except Exception as e:
            print(f"Warning: Could not load config file: {e}")
    
    return config


def create_default_config():
    """Create default configuration file"""
    config_dir = Path.home() / '.subgraph-health'
    config_dir.mkdir(parents=True, exist_ok=True)
    config_file = config_dir / 'config.json'
    
    default_config = {
        'prometheus_url': 'http://localhost:9090',
        'network_subgraph_url': 'https://api.thegraph.com/subgraphs/name/graphprotocol/graph-network-arbitrum',
        'indexer_id': '0x...',
        'allocation_max_days': 28,
        'chain_blocks_per_hour': {
            'mainnet': 300,
            'matic': 1800,
            'arbitrum-one': 15000,
            'base': 1800,
            'bsc': 1200,
            'avalanche': 1800,
            'gnosis': 720,
            'optimism': 1800,
            'celo': 720,
            'fantom': 3600,
            'moonbeam': 500,
            'moonriver': 500,
            'polygon-zkevm': 300,
            'zksync-era': 720,
            'linea': 300,
            'scroll': 300,
            'sonic': 1800,
            'arbitrum-sepolia': 15000
        }
    }
    
    with open(config_file, 'w') as f:
        json.dump(default_config, f, indent=2)
    
    print(f"Created default config at: {config_file}")
    print("Please edit this file with your settings.")


def format_blocks(blocks: int) -> str:
    """Format block count with K/M suffix"""
    if blocks >= 1_000_000:
        return f"{blocks / 1_000_000:.1f}M"
    elif blocks >= 1_000:
        return f"{blocks / 1_000:.1f}K"
    return str(blocks)


def format_tokens(tokens: int) -> str:
    """Format token amount in GRT"""
    grt = tokens / 1e18
    if grt >= 1:
        return f"{grt:,.0f} GRT"
    else:
        return f"{grt:.2f} GRT"


def format_duration(seconds: float) -> str:
    """Format duration in human readable format"""
    if seconds < 0:
        return "expired"
    
    if seconds < 3600:
        return f"{int(seconds / 60)}m"
    elif seconds < 86400:
        return f"{int(seconds / 3600)}h"
    else:
        days = int(seconds / 86400)
        hours = int((seconds % 86400) / 3600)
        return f"{days}d {hours}h"


def check_other_indexers_status(
    deployment_ipfs: str, 
    our_head: int,
    other_indexers: List[Dict],
    cache: Cache
) -> Tuple[int, int, Optional[int]]:
    """Check status of other indexers for a deployment
    Returns: (healthy_count, failed_count, common_fail_block)
    """
    healthy = 0
    failed = 0
    fail_blocks = []
    
    for indexer in other_indexers:
        indexer_url = indexer.get('url', '')
        if not indexer_url:
            continue
        
        # Clean up URL
        if not indexer_url.startswith('http'):
            indexer_url = f"https://{indexer_url}"
        indexer_url = indexer_url.rstrip('/')
        
        # Check cache first
        cache_key = f"status_{indexer['id']}_{deployment_ipfs}"
        cached = cache.get(cache_key)
        if cached is not None:
            if cached.get('healthy'):
                # Only count as healthy if they're at or ahead of our block
                if cached.get('latestBlock', 0) >= our_head:
                    healthy += 1
                else:
                    # They're behind us, don't count as healthy or failed
                    pass
            elif cached.get('failed'):
                failed += 1
                if cached.get('latestBlock'):
                    fail_blocks.append(cached['latestBlock'])
            continue
        
        # Query the indexer's status endpoint
        status_url = f"{indexer_url}/status"
        try:
            response = requests.post(
                status_url,
                json={
                    'query': '''
                    query($deployment: String!) {
                        indexingStatuses(subgraphs: [$deployment]) {
                            synced
                            health
                            fatalError { message block { number } }
                            chains { latestBlock { number } chainHeadBlock { number } }
                        }
                    }
                    ''',
                    'variables': {'deployment': deployment_ipfs}
                },
                timeout=10
            )
            response.raise_for_status()
            data = response.json()
            
            statuses = data.get('data', {}).get('indexingStatuses', [])
            if statuses:
                status = statuses[0]
                health = status.get('health', '')
                latest_block = 0
                
                chains = status.get('chains', [])
                if chains:
                    latest_block = int(chains[0].get('latestBlock', {}).get('number', 0) or 0)
                
                result = {'latestBlock': latest_block}
                
                if health == 'failed':
                    failed += 1
                    result['failed'] = True
                    if latest_block:
                        fail_blocks.append(latest_block)
                elif health == 'healthy' and latest_block >= our_head:
                    # Only count as healthy if they're at or ahead of us
                    healthy += 1
                    result['healthy'] = True
                else:
                    # Behind us or unknown - don't count
                    result['unknown'] = True
                
                cache.set(cache_key, result)
            else:
                cache.set(cache_key, {'unknown': True})
        except:
            cache.set(cache_key, {'unknown': True})
    
    # Determine common fail block (if all failures are at similar block)
    common_fail_block = None
    if fail_blocks:
        # Check if all fail blocks are within 100 blocks of each other
        min_block = min(fail_blocks)
        max_block = max(fail_blocks)
        if max_block - min_block < 100:
            common_fail_block = min_block
    
    return healthy, failed, common_fail_block


def check_deployment_health(
    deployment: Dict,
    prom_metrics: Dict,
    chain_heads: Dict[str, int],
    config: Dict,
    network_client: NetworkSubgraphClient,
    cache: Cache
) -> Dict:
    """Check health of a single deployment"""
    ipfs_hash = deployment.get('subgraphDeployment', {}).get('ipfsHash', '')
    network = deployment.get('subgraphDeployment', {}).get('manifest', {}).get('network', '')
    allocated = int(deployment.get('allocatedTokens', 0))
    created_at = int(deployment.get('createdAt', 0))
    
    # Get subgraph ID for explorer links
    versions = deployment.get('subgraphDeployment', {}).get('versions', [])
    subgraph_id = None
    if versions and len(versions) > 0:
        subgraph_id = versions[0].get('subgraph', {}).get('id')
    
    result = {
        'info': {
            'ipfsHash': ipfs_hash,
            'subgraphId': subgraph_id,
            'network': network,
            'allocatedTokens': allocated,
            'createdAt': created_at
        },
        'issues': [],
        'warnings': []
    }
    
    # Get metrics for this deployment
    metrics = prom_metrics.get(ipfs_hash, {})
    
    if not metrics:
        result['warnings'].append('No Prometheus metrics found')
        return result
    
    head = metrics.get('head')
    status = metrics.get('status')
    blocks_per_hour = metrics.get('blocks_per_hour')
    
    # Get chain head for this network
    chain_head = chain_heads.get(network) if network else None
    expected_bph = config.get('chain_blocks_per_hour', {}).get(network, 300) if network else 300
    
    # Calculate real gap
    gap = 0
    if head and chain_head and chain_head > head:
        gap = chain_head - head
        result['info']['gap'] = gap
        result['info']['chainHead'] = chain_head
    
    # Check for indexing failure (status=failed OR stuck with 0 blocks/hour and significant gap)
    is_stuck = blocks_per_hour is not None and blocks_per_hour < 10 and gap > 1000
    is_failed = status == 'failed' or is_stuck
    
    if is_failed:
        result['issues'].append({
            'type': 'error',
            'message': 'Indexing failed' if status == 'failed' else 'Indexing stuck (0 blocks/hour)'
        })
        result['info']['blocksPerHour'] = blocks_per_hour
        
        # Check other indexers
        other_indexers = network_client.get_other_indexers_for_deployment(
            ipfs_hash, 
            config.get('indexer_id', '')
        )
        
        if other_indexers:
            healthy, failed, common_fail_block = check_other_indexers_status(
                ipfs_hash, 
                head or 0,
                other_indexers,
                cache
            )
            result['info']['othersHealthy'] = healthy > 0
            result['info']['othersFailed'] = failed
            result['info']['othersHealthyCount'] = healthy
            result['info']['commonFailBlock'] = common_fail_block
            
            # Determine if it's at same block as others
            if common_fail_block and head:
                if abs(head - common_fail_block) < 100:
                    result['info']['sameBlockFailure'] = True
        
        return result
    
    # Check for gap growing (only if we have a significant gap and low throughput, but not stuck)
    if head and chain_head and gap > 1000:
        result['info']['blocksPerHour'] = blocks_per_hour
        
        if blocks_per_hour is not None and blocks_per_hour < expected_bph * 0.8:
            # We have a gap AND we're not catching up fast enough
            result['issues'].append({
                'type': 'gap_growing',
                'message': f'Gap growing: {format_blocks(gap)} behind, {blocks_per_hour:.0f}/h vs expected {expected_bph}/h'
            })
    
    # Check for sync too slow
    if head and created_at and gap > 0:
        # Estimate remaining sync time
        allocation_created = datetime.fromtimestamp(created_at)
        allocation_max = timedelta(days=config.get('allocation_max_days', 28))
        allocation_end = allocation_created + allocation_max
        remaining_time = (allocation_end - datetime.now()).total_seconds()
        
        result['info']['allocationRemaining'] = remaining_time
        
        if blocks_per_hour and blocks_per_hour > 0:
            hours_to_sync = gap / blocks_per_hour
            sync_time_seconds = hours_to_sync * 3600
            
            result['info']['estimatedSyncTime'] = sync_time_seconds
            result['info']['blocksPerHour'] = blocks_per_hour
            
            if sync_time_seconds > remaining_time and remaining_time > 0:
                result['issues'].append({
                    'type': 'sync_too_slow',
                    'message': f'Sync time ({format_duration(sync_time_seconds)}) > allocation remaining ({format_duration(remaining_time)})'
                })
    
    return result


def print_item(result: Dict, history_manager: HistoryManager, ack_manager: AcknowledgementManager, show_ack: bool = False):
    """Print a single health check result"""
    info = result.get('info', {})
    issues = result.get('issues', [])
    warnings = result.get('warnings', [])
    
    ipfs_hash = info.get('ipfsHash', '')
    subgraph_id = info.get('subgraphId')
    network = info.get('network', 'unknown')
    allocated = info.get('allocatedTokens', 0)
    
    # Get duration from history
    duration = history_manager.get_issue_duration(ipfs_hash)
    duration_str = f" [{duration}]" if duration else ""
    
    # Check acknowledgement
    ack = ack_manager.is_acknowledged(ipfs_hash)
    ack_str = ""
    if ack:
        category = ack.get('category', 'wip')
        reason = ack.get('reason', '')
        if reason:
            ack_str = f" [{category}] \"{reason}\""
        else:
            ack_str = f" [{category}]"
    
    # Format hash as clickable link
    hash_display = format_deployment_link(ipfs_hash, subgraph_id)
    
    print(f"  {hash_display}{Colors.DIM}{duration_str}{Colors.RESET}{Colors.YELLOW}{ack_str}{Colors.RESET}")
    
    # Build detail line
    details = [network, format_tokens(allocated)]
    
    # Add gap info
    gap = info.get('gap')
    if gap:
        details.append(f"{format_blocks(gap)} behind")
    
    # Add blocks per hour if relevant
    bph = info.get('blocksPerHour')
    if bph is not None:
        details.append(f"{format_blocks(int(bph))}/h")
    
    # Add sync time info
    sync_time = info.get('estimatedSyncTime')
    remaining = info.get('allocationRemaining')
    if sync_time and remaining:
        details.append(f"sync:{format_duration(sync_time)} remain:{format_duration(remaining)}")
    
    # Add other indexers info
    if 'othersHealthyCount' in info or 'othersFailed' in info:
        healthy = info.get('othersHealthyCount', 0)
        failed = info.get('othersFailed', 0)
        details.append(f"others: {healthy}✓ {failed}✗")
    
    # Add common fail block info
    if info.get('sameBlockFailure'):
        common_block = info.get('commonFailBlock')
        if common_block:
            details.append(f"@ block {common_block:,}")
    
    print(f"    {' | '.join(details)}")


def main():
    parser = argparse.ArgumentParser(description='Monitor subgraph health')
    parser.add_argument('--init', action='store_true', help='Create default config file')
    parser.add_argument('--ack', metavar='IPFS_HASH', help='Acknowledge an issue')
    parser.add_argument('--unack', metavar='IPFS_HASH', help='Remove acknowledgement')
    parser.add_argument('--list-ack', action='store_true', help='List all acknowledgements')
    parser.add_argument('--show-ack', action='store_true', help='Show acknowledged issues in report')
    parser.add_argument('--reason', default='', help='Reason for acknowledgement')
    parser.add_argument('--category', default='wip', choices=['ignore', 'wip', 'external'], 
                       help='Category for acknowledgement')
    parser.add_argument('--expires', help='Expiry date for acknowledgement (ISO format)')
    args = parser.parse_args()
    
    if args.init:
        create_default_config()
        return
    
    config = get_config()
    config_dir = Path.home() / '.subgraph-health'
    config_dir.mkdir(parents=True, exist_ok=True)
    
    ack_manager = AcknowledgementManager(config_dir)
    history_manager = HistoryManager(config_dir)
    
    # Handle acknowledgement commands
    if args.ack:
        if ack_manager.acknowledge(args.ack, args.reason, args.category, args.expires):
            print(f"Acknowledged: {args.ack}")
        return
    
    if args.unack:
        if ack_manager.unacknowledge(args.unack):
            print(f"Removed acknowledgement: {args.unack}")
        else:
            print(f"Not found: {args.unack}")
        return
    
    if args.list_ack:
        acks = ack_manager.list_all()
        if not acks:
            print("No acknowledgements")
            return
        print(f"\n{Colors.BOLD}Acknowledged Issues:{Colors.RESET}\n")
        for ipfs_hash, ack in acks.items():
            category = ack.get('category', 'wip')
            reason = ack.get('reason', '')
            expires = ack.get('expires', 'never')
            print(f"  {ipfs_hash}")
            print(f"    Category: {category}, Expires: {expires}")
            if reason:
                print(f"    Reason: {reason}")
        return
    
    indexer_id = config.get('indexer_id')
    if not indexer_id or indexer_id == '0x...':
        print("Error: indexer_id not configured")
        print("Run with --init to create config file, or set INDEXER_ID environment variable")
        return
    
    print(f"Checking subgraph health for indexer: {indexer_id}")
    print(f"Prometheus: {config.get('prometheus_url')}")
    
    # Initialize clients
    prom = PrometheusClient(config.get('prometheus_url'))
    network_client = NetworkSubgraphClient(config.get('network_subgraph_url'))
    cache = Cache(config_dir / 'cache', ttl_seconds=300)
    
    # Get allocations
    allocations = network_client.get_indexer_allocations(indexer_id)
    print(f"Found {len(allocations)} active allocations")
    
    if not allocations:
        print("No active allocations found")
        return
    
    # Get all Prometheus metrics at once
    prom_metrics = prom.get_all_deployment_metrics()
    chain_heads = prom.get_all_chain_heads()
    
    # Get blocks per hour using rate of deployment_head change (more accurate for sync estimation)
    bph_results = prom.query('rate(deployment_head[10m]) * 3600')
    if bph_results:
        for r in bph_results:
            dep = r.get('metric', {}).get('deployment', '')
            if dep and dep in prom_metrics:
                try:
                    prom_metrics[dep]['blocks_per_hour'] = float(r['value'][1])
                except:
                    pass
    
    # Check each allocation
    results = []
    for alloc in allocations:
        result = check_deployment_health(alloc, prom_metrics, chain_heads, config, network_client, cache)
        results.append(result)
    
    # Record run and get changes
    changes = history_manager.record_run(results)
    
    # Categorize results
    healthy = []
    failed_our_issue = []
    failed_same_block = []
    failed_subgraph = []
    failed_unknown = []
    sync_too_slow = []
    gap_growing = []
    no_metrics = []
    acknowledged_count = 0
    
    for result in results:
        info = result.get('info', {})
        issues = result.get('issues', [])
        warnings = result.get('warnings', [])
        ipfs_hash = info.get('ipfsHash', '')
        
        # Check if acknowledged
        is_acked = ack_manager.is_acknowledged(ipfs_hash)
        if is_acked and not args.show_ack:
            acknowledged_count += 1
            continue
        
        if not issues and not warnings:
            healthy.append(result)
        elif 'No Prometheus metrics found' in warnings:
            no_metrics.append(result)
        else:
            issue_types = [i.get('type') for i in issues]
            if 'error' in issue_types:
                if info.get('othersHealthy') == True:
                    failed_our_issue.append(result)
                elif info.get('sameBlockFailure'):
                    failed_same_block.append(result)
                elif info.get('othersHealthy') == False:
                    failed_subgraph.append(result)
                else:
                    failed_unknown.append(result)
            elif 'sync_too_slow' in issue_types:
                sync_too_slow.append(result)
            elif 'gap_growing' in issue_types:
                gap_growing.append(result)
    
    # Print summary
    print(f"\n\n{Colors.BOLD}Subgraph Health Report{Colors.RESET}")
    print("=" * 70)
    
    total_issues = (len(failed_our_issue) + len(failed_same_block) + len(failed_subgraph) + 
                   len(failed_unknown) + len(sync_too_slow) + len(gap_growing) + len(no_metrics))
    
    print(f"  {Colors.GREEN}Healthy: {len(healthy)}{Colors.RESET}")
    if failed_our_issue or failed_same_block or failed_subgraph or failed_unknown:
        failed_total = len(failed_our_issue) + len(failed_same_block) + len(failed_subgraph) + len(failed_unknown)
        print(f"  {Colors.RED}Indexing Failed: {failed_total}{Colors.RESET}")
    if sync_too_slow:
        print(f"  {Colors.RED}Sync Too Slow: {len(sync_too_slow)}{Colors.RESET}")
    if gap_growing:
        print(f"  {Colors.YELLOW}Gap Growing: {len(gap_growing)}{Colors.RESET}")
    if no_metrics:
        print(f"  {Colors.YELLOW}No Metrics: {len(no_metrics)}{Colors.RESET}")
    if acknowledged_count > 0:
        print(f"  {Colors.DIM}(includes {acknowledged_count} acknowledged){Colors.RESET}")
    
    # Print changes
    new_issues = changes.get('new', [])
    resolved_issues = changes.get('resolved', [])
    
    if new_issues or resolved_issues:
        print(f"\n{Colors.BOLD}Changes since last run:{Colors.RESET}")
        print("-" * 70)
        
        if resolved_issues:
            print(f"  {Colors.GREEN}✓ {len(resolved_issues)} resolved:{Colors.RESET}")
            for issue in resolved_issues[:10]:  # Limit to 10
                duration = issue.get('duration', '?')
                print(f"    {issue['ipfsHash']} (was failing for {duration})")
            if len(resolved_issues) > 10:
                print(f"    ... and {len(resolved_issues) - 10} more")
        
        if new_issues:
            print(f"  {Colors.RED}✗ {len(new_issues)} new issues:{Colors.RESET}")
            for issue in new_issues[:20]:  # Limit to 20
                issue_type = issue.get('type', 'unknown').replace('_', ' ')
                print(f"    {issue['ipfsHash']} ({issue_type})")
            if len(new_issues) > 20:
                print(f"    ... and {len(new_issues) - 20} more")
        print()
    
    # Print detailed sections
    if failed_our_issue:
        print(f"\n{Colors.RED}{Colors.BOLD}✗ INDEXING FAILED - OUR ISSUE ({len(failed_our_issue)}){Colors.RESET}")
        print(f"  {Colors.DIM}(Other indexers are healthy - problem is on our side){Colors.RESET}")
        print("-" * 70)
        for result in failed_our_issue:
            print_item(result, history_manager, ack_manager, args.show_ack)
    
    if failed_same_block:
        print(f"\n{Colors.RED}{Colors.BOLD}✗ INDEXING FAILED - SUBGRAPH ISSUE (same block) ({len(failed_same_block)}){Colors.RESET}")
        print(f"  {Colors.DIM}(All indexers failing at the same block - definite subgraph bug){Colors.RESET}")
        print("-" * 70)
        for result in failed_same_block:
            print_item(result, history_manager, ack_manager, args.show_ack)
    
    if failed_subgraph:
        print(f"\n{Colors.RED}{Colors.BOLD}✗ INDEXING FAILED - SUBGRAPH ISSUE ({len(failed_subgraph)}){Colors.RESET}")
        print(f"  {Colors.DIM}(All indexers failing - likely a subgraph problem){Colors.RESET}")
        print("-" * 70)
        for result in failed_subgraph:
            print_item(result, history_manager, ack_manager, args.show_ack)
    
    if failed_unknown:
        print(f"\n{Colors.RED}{Colors.BOLD}✗ INDEXING FAILED - UNKNOWN ({len(failed_unknown)}){Colors.RESET}")
        print(f"  {Colors.DIM}(Could not check other indexers){Colors.RESET}")
        print("-" * 70)
        for result in failed_unknown:
            print_item(result, history_manager, ack_manager, args.show_ack)
    
    if sync_too_slow:
        print(f"\n{Colors.RED}{Colors.BOLD}✗ SYNC TOO SLOW ({len(sync_too_slow)}){Colors.RESET}")
        print("-" * 70)
        for result in sync_too_slow:
            print_item(result, history_manager, ack_manager, args.show_ack)
    
    if gap_growing:
        print(f"\n{Colors.YELLOW}{Colors.BOLD}⚠ GAP GROWING ({len(gap_growing)}){Colors.RESET}")
        print("-" * 70)
        for result in gap_growing:
            print_item(result, history_manager, ack_manager, args.show_ack)
    
    if no_metrics:
        print(f"\n{Colors.YELLOW}{Colors.BOLD}⚠ NO METRICS ({len(no_metrics)}){Colors.RESET}")
        print("-" * 70)
        for result in no_metrics:
            print_item(result, history_manager, ack_manager, args.show_ack)


if __name__ == '__main__':
    main()

