#!/usr/bin/env python3
"""
indexerinfo - Display indexer information from The Graph Network

Usage:
    indexerinfo <search_term>
    
Search by:
    - Partial ENS name (e.g., "ellipfra", "pinax")
    - Partial address (e.g., "0xf92f", "f92f430")
    - Partial URL (e.g., "staked.cloud")

Configuration:
    - Environment variable: THEGRAPH_NETWORK_SUBGRAPH_URL
    - Config file: ~/.subinfo/config.json (key "network_subgraph_url")
"""

import sys
import json
import argparse
import os
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import requests
from pathlib import Path


class Colors:
    """ANSI color codes for terminal output"""
    RESET = '\033[0m'
    BOLD = '\033[1m'
    DIM = '\033[2m'
    
    # Standard colors
    RED = '\033[31m'
    GREEN = '\033[32m'
    YELLOW = '\033[33m'
    BLUE = '\033[34m'
    MAGENTA = '\033[35m'
    CYAN = '\033[36m'
    WHITE = '\033[37m'
    
    # Bright colors
    BRIGHT_RED = '\033[91m'
    BRIGHT_GREEN = '\033[92m'
    BRIGHT_YELLOW = '\033[93m'
    BRIGHT_BLUE = '\033[94m'
    BRIGHT_MAGENTA = '\033[95m'
    BRIGHT_CYAN = '\033[96m'


def terminal_link(url: str, text: str) -> str:
    """Create a clickable terminal hyperlink (OSC 8)"""
    return f'\033]8;;{url}\033\\{text}\033]8;;\033\\'


def format_deployment_link(ipfs_hash: str, subgraph_id: str = None) -> str:
    """Format IPFS hash as a clickable link to The Graph Explorer"""
    if subgraph_id:
        url = f"https://thegraph.com/explorer/subgraphs/{subgraph_id}?view=Query&chain=arbitrum-one"
        return terminal_link(url, ipfs_hash)
    return ipfs_hash


def get_subgraph_id_from_deployment(deployment: Dict) -> Optional[str]:
    """Extract subgraph ID from deployment data"""
    versions = deployment.get('versions', [])
    if versions:
        return versions[0].get('subgraph', {}).get('id')
    return None


class TheGraphClient:
    """Client to query The Graph Network subgraph"""
    
    def __init__(self, network_subgraph_url: str):
        self.network_subgraph_url = network_subgraph_url.rstrip('/')
    
    def query(self, query: str, variables: Optional[Dict] = None) -> Dict:
        """Execute a GraphQL query"""
        try:
            response = requests.post(
                self.network_subgraph_url,
                json={'query': query, 'variables': variables or {}},
                headers={'Content-Type': 'application/json'},
                timeout=30
            )
            response.raise_for_status()
            data = response.json()
            if 'errors' in data:
                return {}
            return data.get('data', {})
        except Exception as e:
            print(f"{Colors.RED}Query error: {e}{Colors.RESET}", file=sys.stderr)
            return {}
    
    def search_indexers(self, search_term: str) -> List[Dict]:
        """Search for indexers by partial name, address or URL"""
        results = []
        search_lower = search_term.lower()
        
        # If it looks like an address (starts with 0x or is hex)
        if search_lower.startswith('0x') or all(c in '0123456789abcdef' for c in search_lower):
            # Search by address prefix using range query
            addr_search = search_lower if search_lower.startswith('0x') else f"0x{search_lower}"
            # Pad to create a range: 0x8bbe -> 0x8bbe0000... to 0x8bbeffff...
            addr_min = addr_search.ljust(42, '0')
            addr_max = addr_search.ljust(42, 'f')
            
            query = f"""
            {{
                indexers(
                    where: {{ id_gte: "{addr_min}", id_lte: "{addr_max}" }}
                    first: 10
                    orderBy: stakedTokens
                    orderDirection: desc
                ) {{
                    id
                    url
                    stakedTokens
                    delegatedTokens
                    allocatedTokens
                    indexingRewardCut
                    queryFeeCut
                    indexingRewardEffectiveCut
                    queryFeeEffectiveCut
                    delegatorShares
                    allocationCount
                }}
            }}
            """
            result = self.query(query)
            results.extend(result.get('indexers', []))
        
        # Search by URL containing the term
        if not results:
            query = """
            query SearchByUrl($search: String!) {
                indexers(
                    where: { url_contains: $search }
                    first: 10
                    orderBy: stakedTokens
                    orderDirection: desc
                ) {
                    id
                    url
                    stakedTokens
                    delegatedTokens
                    allocatedTokens
                    indexingRewardCut
                    queryFeeCut
                    indexingRewardEffectiveCut
                    queryFeeEffectiveCut
                    delegatorShares
                    allocationCount
                }
            }
            """
            result = self.query(query, {'search': search_lower})
            results.extend(result.get('indexers', []))
        
        return results
    
    def get_indexer_details(self, indexer_id: str) -> Optional[Dict]:
        """Get detailed information about an indexer"""
        query = """
        query GetIndexer($id: String!) {
            indexer(id: $id) {
                id
                url
                stakedTokens
                delegatedTokens
                delegatedCapacity
                allocatedTokens
                availableStake
                tokenCapacity
                lockedTokens
                unstakedTokens
                indexingRewardCut
                queryFeeCut
                indexingRewardEffectiveCut
                queryFeeEffectiveCut
                delegatorShares
                delegatorIndexingRewards
                delegatorQueryFees
                delegationExchangeRate
                allocationCount
                totalAllocationCount
                createdAt
            }
        }
        """
        result = self.query(query, {'id': indexer_id.lower()})
        return result.get('indexer')
    
    def get_indexer_allocations(self, indexer_id: str, hours: int = 48) -> Tuple[List[Dict], List[Dict]]:
        """Get active allocations and recent closed allocations for an indexer"""
        cutoff_time = int((datetime.now() - timedelta(hours=hours)).timestamp())
        
        # Active allocations
        active_query = """
        query GetActiveAllocations($indexer: String!) {
            allocations(
                where: { indexer: $indexer, status: Active }
                orderBy: createdAt
                orderDirection: desc
                first: 100
            ) {
                id
                allocatedTokens
                createdAt
                status
                subgraphDeployment {
                    ipfsHash
                    signalledTokens
                    versions(first: 1, orderBy: createdAt, orderDirection: desc) {
                        subgraph { id }
                    }
                }
            }
        }
        """
        active_result = self.query(active_query, {'indexer': indexer_id.lower()})
        active = active_result.get('allocations', [])
        
        # Recent closed allocations
        closed_query = f"""
        {{
            allocations(
                where: {{ indexer: "{indexer_id.lower()}", status: Closed, closedAt_gte: {cutoff_time} }}
                orderBy: closedAt
                orderDirection: desc
                first: 100
            ) {{
                id
                allocatedTokens
                createdAt
                closedAt
                status
                indexingRewards
                subgraphDeployment {{
                    ipfsHash
                    signalledTokens
                    versions(first: 1, orderBy: createdAt, orderDirection: desc) {{
                        subgraph {{ id }}
                    }}
                }}
            }}
        }}
        """
        closed_result = self.query(closed_query)
        closed = closed_result.get('allocations', [])
        
        return active, closed
    
    def get_indexer_poi_submissions(self, indexer_id: str, hours: int = 48) -> List[Dict]:
        """Get POI submissions (reward collections) for an indexer"""
        cutoff_time = int((datetime.now() - timedelta(hours=hours)).timestamp())
        
        query = f"""
        {{
            poiSubmissions(
                where: {{ 
                    allocation_: {{ indexer: "{indexer_id.lower()}", status: Active }}
                    presentedAtTimestamp_gte: {cutoff_time}
                }}
                orderBy: presentedAtTimestamp
                orderDirection: desc
                first: 100
            ) {{
                id
                presentedAtTimestamp
                allocation {{
                    id
                    status
                    allocatedTokens
                    indexingRewards
                    subgraphDeployment {{
                        ipfsHash
                        versions(first: 1, orderBy: createdAt, orderDirection: desc) {{
                            subgraph {{ id }}
                        }}
                    }}
                }}
            }}
        }}
        """
        result = self.query(query)
        return result.get('poiSubmissions', [])
    
    def get_top_allocations(self, indexer_id: str, limit: int = 10) -> List[Dict]:
        """Get top allocations by size for an indexer"""
        query = f"""
        {{
            allocations(
                where: {{ indexer: "{indexer_id.lower()}", status: Active }}
                orderBy: allocatedTokens
                orderDirection: desc
                first: {limit}
            ) {{
                id
                allocatedTokens
                createdAt
                status
                subgraphDeployment {{
                    ipfsHash
                    signalledTokens
                    versions(first: 1, orderBy: createdAt, orderDirection: desc) {{
                        subgraph {{ id }}
                    }}
                }}
            }}
        }}
        """
        result = self.query(query)
        return result.get('allocations', [])
    
    def get_network_stats(self) -> Dict:
        """Get network-wide statistics for APR calculation"""
        query = """
        {
            graphNetwork(id: "1") {
                totalTokensAllocated
                totalTokensSignalled
                networkGRTIssuancePerBlock
            }
        }
        """
        result = self.query(query)
        return result.get('graphNetwork', {})
    
    def get_all_active_allocations(self, indexer_id: str) -> List[Dict]:
        """Get all active allocations with signal data for APR calculation"""
        query = f"""
        {{
            allocations(
                where: {{ indexer: "{indexer_id.lower()}", status: Active }}
                first: 1000
            ) {{
                allocatedTokens
                subgraphDeployment {{
                    signalledTokens
                    stakedTokens
                }}
            }}
        }}
        """
        result = self.query(query)
        return result.get('allocations', [])
    
    def get_delegation_events(self, indexer_id: str, hours: int = 48) -> Tuple[List[Dict], List[Dict]]:
        """Get recent delegation/undelegation events for an indexer"""
        cutoff_time = int((datetime.now() - timedelta(hours=hours)).timestamp())
        
        # Get recent delegations (based on lastDelegatedAt)
        delegation_query = f"""
        {{
            delegatedStakes(
                where: {{ indexer: "{indexer_id.lower()}", lastDelegatedAt_gte: {cutoff_time} }}
                orderBy: lastDelegatedAt
                orderDirection: desc
                first: 100
            ) {{
                id
                delegator {{ id }}
                stakedTokens
                createdAt
                lastDelegatedAt
            }}
        }}
        """
        delegations = self.query(delegation_query).get('delegatedStakes', [])
        
        # Get recent undelegations (based on lastUndelegatedAt)
        # lockedTokens = amount in thawing period after undelegation
        undelegation_query = f"""
        {{
            delegatedStakes(
                where: {{ indexer: "{indexer_id.lower()}", lastUndelegatedAt_gte: {cutoff_time} }}
                orderBy: lastUndelegatedAt
                orderDirection: desc
                first: 100
            ) {{
                id
                delegator {{ id }}
                stakedTokens
                lockedTokens
                lastUndelegatedAt
            }}
        }}
        """
        undelegations = self.query(undelegation_query).get('delegatedStakes', [])
        
        return delegations, undelegations


class ENSClient:
    """Client to resolve ENS names"""
    
    def __init__(self, ens_subgraph_url: str):
        self.ens_subgraph_url = ens_subgraph_url.rstrip('/')
        self._cache = {}
        self._cache_file = Path.home() / '.subinfo' / 'ens_cache.json'
        self._load_cache()
    
    def _load_cache(self):
        try:
            if self._cache_file.exists():
                with open(self._cache_file, 'r') as f:
                    cache_data = json.load(f)
                    now = time.time()
                    for addr, entry in cache_data.items():
                        if isinstance(entry, dict) and 'name' in entry:
                            self._cache[addr] = entry
                        elif isinstance(entry, str) or entry is None:
                            self._cache[addr] = {'name': entry, 'timestamp': now}
        except:
            pass
    
    def query(self, query: str, variables: Optional[Dict] = None) -> Dict:
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
        if not address or address == 'Unknown':
            return None
        
        address_lower = address.lower()
        if address_lower in self._cache:
            entry = self._cache[address_lower]
            if isinstance(entry, dict):
                return entry.get('name')
            return entry
        
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
                    self._cache[address_lower] = {'name': name, 'timestamp': time.time()}
                    return name
        except:
            pass
        
        self._cache[address_lower] = {'name': None, 'timestamp': time.time()}
        return None
    
    def search_by_ens(self, partial_name: str) -> List[Dict]:
        """Search for ENS names containing the partial name"""
        query = """
        query SearchENS($search: String!) {
            domains(
                where: { name_contains: $search }
                first: 20
                orderBy: createdAt
                orderDirection: desc
            ) {
                name
                resolvedAddress { id }
            }
        }
        """
        result = self.query(query, {'search': partial_name.lower()})
        return result.get('domains', [])


def format_tokens(tokens: str) -> str:
    """Format token amount with thousands separator"""
    try:
        amount = float(tokens) / 1e18
        if amount >= 1:
            return f"{amount:,.0f} GRT"
        elif amount > 0:
            return f"{amount:.2f} GRT"
        elif amount < 0:
            # Negative values (e.g., over-allocated)
            return f"{amount:,.0f} GRT"
        else:
            return "0 GRT"
    except:
        return "0 GRT"


def format_tokens_short(tokens: str) -> str:
    """Format token amount in short form (k, M)"""
    try:
        amount = float(tokens) / 1e18
        if amount >= 1_000_000:
            return f"{amount/1_000_000:.1f}M"
        elif amount >= 1_000:
            return f"{amount/1_000:.0f}k"
        elif amount >= 1:
            return f"{amount:,.0f}"
        else:
            return f"{amount:.2f}"
    except:
        return "0"


def format_percentage(value: float) -> str:
    """Format a PPM value as percentage"""
    return f"{value / 10000:.2f}%"


def format_timestamp(ts: str) -> str:
    try:
        dt = datetime.fromtimestamp(int(ts))
        return dt.strftime('%Y-%m-%d %H:%M')
    except:
        return 'Unknown'


def format_duration(seconds: int) -> str:
    """Format duration in human readable format"""
    if seconds < 0:
        return "expired"
    days = seconds // 86400
    hours = (seconds % 86400) // 3600
    if days > 0:
        return f"{days}d {hours}h"
    elif hours > 0:
        return f"{hours}h"
    else:
        minutes = (seconds % 3600) // 60
        return f"{minutes}m"


def print_section(title: str):
    """Display a compact section title"""
    print(f"\n{Colors.CYAN}▸ {title}{Colors.RESET}")


def get_network_subgraph_url() -> str:
    """Get network subgraph URL from environment variable or config"""
    env_url = os.environ.get('THEGRAPH_NETWORK_SUBGRAPH_URL')
    if env_url:
        return env_url.rstrip('/')
    
    config_file = Path.home() / '.subinfo' / 'config.json'
    if config_file.exists():
        try:
            with open(config_file, 'r') as f:
                content = f.read().strip()
                if content:
                    config = json.loads(content)
                    url = config.get('network_subgraph_url')
                    if url:
                        return url.rstrip('/')
        except:
            pass
    
    return None


def get_ens_subgraph_url() -> str:
    """Get ENS subgraph URL"""
    env_url = os.environ.get('ENS_SUBGRAPH_URL')
    if env_url:
        return env_url.rstrip('/')
    
    config_file = Path.home() / '.subinfo' / 'config.json'
    if config_file.exists():
        try:
            with open(config_file, 'r') as f:
                config = json.loads(f.read())
                url = config.get('ens_subgraph_url')
                if url:
                    return url.rstrip('/')
        except:
            pass
    
    return None


def main():
    parser = argparse.ArgumentParser(
        description='Display indexer information from The Graph Network',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    indexerinfo ellipfra           # Search by partial ENS name
    indexerinfo 0xf92f             # Search by address prefix
    indexerinfo staked.cloud       # Search by URL
        """
    )
    parser.add_argument('search_term', help='Search term (ENS name, address, or URL)')
    parser.add_argument('--hours', type=int, default=48, help='Hours of history to show (default: 48)')
    
    args = parser.parse_args()
    
    network_url = get_network_subgraph_url()
    if not network_url:
        print("Error: Network subgraph URL not configured.", file=sys.stderr)
        sys.exit(1)
    
    ens_url = get_ens_subgraph_url()
    
    client = TheGraphClient(network_url)
    ens_client = ENSClient(ens_url) if ens_url else None
    
    # Search for indexers
    indexers = []
    
    # First try ENS search if it doesn't look like an address
    search_term = args.search_term
    if ens_client and not search_term.startswith('0x') and not all(c in '0123456789abcdef' for c in search_term.lower()):
        ens_results = ens_client.search_by_ens(search_term)
        for domain in ens_results:
            resolved = domain.get('resolvedAddress', {})
            if resolved:
                addr = resolved.get('id')
                if addr:
                    # Check if this address is an indexer
                    indexer = client.get_indexer_details(addr)
                    if indexer:
                        indexer['ens_name'] = domain.get('name')
                        indexers.append(indexer)
    
    # Then try direct search
    if not indexers:
        indexers = client.search_indexers(search_term)
    
    if not indexers:
        print(f"{Colors.RED}No indexer found matching '{search_term}'{Colors.RESET}")
        sys.exit(1)
    
    if len(indexers) > 1:
        print(f"{Colors.YELLOW}Multiple indexers found:{Colors.RESET}")
        for i, idx in enumerate(indexers[:10]):
            addr = idx.get('id', '')
            ens = idx.get('ens_name') or (ens_client.resolve_address(addr) if ens_client else None)
            url = idx.get('url', '')[:40]
            stake = format_tokens_short(idx.get('stakedTokens', '0'))
            name_display = f"{ens} " if ens else ""
            print(f"  {i+1}. {name_display}({addr[:10]}...) - {stake} GRT - {url}")
        
        try:
            choice = input(f"\n{Colors.CYAN}Select indexer (1-{min(len(indexers), 10)}): {Colors.RESET}")
            idx = int(choice) - 1
            if 0 <= idx < len(indexers):
                indexer = indexers[idx]
            else:
                sys.exit(1)
        except (ValueError, EOFError):
            sys.exit(1)
    else:
        indexer = indexers[0]
    
    # Get full details if we only have partial info
    indexer_id = indexer.get('id')
    if not indexer.get('createdAt'):
        indexer = client.get_indexer_details(indexer_id) or indexer
    
    # Resolve ENS name
    ens_name = indexer.get('ens_name') or (ens_client.resolve_address(indexer_id) if ens_client else None)
    
    # Display header
    if ens_name:
        print(f"{Colors.BOLD}Indexer:{Colors.RESET} {Colors.BRIGHT_CYAN}{ens_name}{Colors.RESET} ({indexer_id})")
    else:
        print(f"{Colors.BOLD}Indexer:{Colors.RESET} {Colors.CYAN}{indexer_id}{Colors.RESET}")
    
    if indexer.get('url'):
        print(f"{Colors.DIM}{indexer['url']}{Colors.RESET}")
    
    # Stake information
    print_section("Stake")
    self_stake = int(indexer.get('stakedTokens', '0'))
    delegated = int(indexer.get('delegatedTokens', '0'))
    delegated_capacity = int(indexer.get('delegatedCapacity', '0'))
    allocated = int(indexer.get('allocatedTokens', '0'))
    available_stake = int(indexer.get('availableStake', '0'))
    token_capacity = int(indexer.get('tokenCapacity', '0'))
    
    # Delegations in thawing = delegated - delegatedCapacity
    delegations_thawing = delegated - delegated_capacity
    
    # Use tokenCapacity for total usable stake (excludes thawing delegations)
    total_stake = token_capacity if token_capacity > 0 else (self_stake + delegated)
    # Calculate remaining directly (can be negative if over-allocated)
    remaining = total_stake - allocated
    remaining_pct = (remaining / total_stake * 100) if total_stake > 0 else 0
    
    # Delegation capacity (16x multiplier is the protocol default)
    delegation_ratio = 16
    max_delegation = self_stake * delegation_ratio
    # Use delegated_capacity (active delegations) for remaining room
    # Tokens in thawing are leaving, so they free up space
    delegation_remaining = max(0, max_delegation - delegated_capacity)
    delegation_used_pct = (delegated / max_delegation * 100) if max_delegation > 0 else 0
    
    print(f"  Self stake:      {Colors.BRIGHT_GREEN}{format_tokens(str(self_stake))}{Colors.RESET}")
    delegated_str = f"{Colors.BRIGHT_CYAN}{format_tokens(str(delegated))}{Colors.RESET} / {format_tokens(str(max_delegation))} ({delegation_used_pct:.0f}%)"
    if delegations_thawing > 0:
        delegated_str += f" {Colors.DIM}({format_tokens(str(delegations_thawing))} thawing){Colors.RESET}"
    print(f"  Delegated:       {delegated_str}")
    if delegation_remaining > 0:
        print(f"  Delegation room: {Colors.BRIGHT_GREEN}{format_tokens(str(delegation_remaining))}{Colors.RESET}")
    else:
        print(f"  Delegation room: {Colors.BRIGHT_RED}FULL{Colors.RESET}")
    print(f"  {Colors.BOLD}Total:           {format_tokens(str(total_stake))}{Colors.RESET}")
    print(f"  Allocated:       {format_tokens(str(allocated))}")
    if remaining < 0:
        # Over-allocated - show warning
        print(f"  Remaining:       {Colors.BRIGHT_RED}{format_tokens(str(remaining))} ({remaining_pct:.1f}%) ⚠ OVER-ALLOCATED{Colors.RESET}")
    else:
        remaining_color = Colors.BRIGHT_GREEN if remaining_pct < 10 else (Colors.BRIGHT_YELLOW if remaining_pct > 30 else Colors.DIM)
        print(f"  Remaining:       {remaining_color}{format_tokens(str(remaining))} ({remaining_pct:.1f}%){Colors.RESET}")
    
    # Reward cuts
    # Raw cut applies to total rewards, but effective cut on delegators is different
    # Formula: rawcut = 1 - (1 - effective) * delegated / (delegated + stake)
    # Solving for effective: effective = 1 - (1 - rawcut) * (delegated + stake) / delegated
    # Use raw delegated + self_stake (not token_capacity) for this calculation
    print_section("Reward Cuts")
    reward_cut_ppm = int(indexer.get('indexingRewardCut', 0))
    query_cut_ppm = int(indexer.get('queryFeeCut', 0))
    
    raw_reward_cut = reward_cut_ppm / 1_000_000  # Convert PPM to decimal
    raw_query_cut = query_cut_ppm / 1_000_000
    
    # Calculate effective cut on delegators using raw stake values (not adjusted for thawing)
    raw_total = self_stake + delegated
    if delegated > 0:
        effective_reward_cut = 1 - (1 - raw_reward_cut) * raw_total / delegated
        effective_query_cut = 1 - (1 - raw_query_cut) * raw_total / delegated
    else:
        effective_reward_cut = raw_reward_cut
        effective_query_cut = raw_query_cut
    
    print(f"  Indexing rewards: {Colors.BRIGHT_CYAN}{raw_reward_cut*100:.1f}%{Colors.RESET} raw, {Colors.BRIGHT_YELLOW}{effective_reward_cut*100:.1f}%{Colors.RESET} effective on delegators")
    print(f"  Query fees:       {Colors.BRIGHT_CYAN}{raw_query_cut*100:.1f}%{Colors.RESET} raw, {Colors.BRIGHT_YELLOW}{effective_query_cut*100:.1f}%{Colors.RESET} effective on delegators")
    
    # Estimated APR calculation (based on current allocations)
    print_section("Instant APR (current allocations)")
    network_stats = client.get_network_stats()
    all_allocations = client.get_all_active_allocations(indexer_id)
    
    if network_stats and all_allocations:
        # Network data
        issuance_per_block = int(network_stats.get('networkGRTIssuancePerBlock', '0')) / 1e18
        total_signal_network = int(network_stats.get('totalTokensSignalled', '0')) / 1e18
        
        # Ethereum blocks per year (~12s per block)
        eth_blocks_per_year = 2_628_000
        annual_issuance = issuance_per_block * eth_blocks_per_year
        
        # Calculate expected rewards by summing each allocation's contribution
        # Formula: reward = annual_issuance × (signal_subgraph / total_signal_network) × (allocation / staked_on_subgraph)
        total_alloc = 0
        total_expected_rewards = 0
        
        for a in all_allocations:
            alloc = int(a.get('allocatedTokens', '0')) / 1e18
            signal = int(a.get('subgraphDeployment', {}).get('signalledTokens', '0')) / 1e18
            staked = int(a.get('subgraphDeployment', {}).get('stakedTokens', '0')) / 1e18
            
            total_alloc += alloc
            
            if staked > 0 and total_signal_network > 0:
                # Subgraph's share of total rewards
                subgraph_share = signal / total_signal_network
                # Indexer's share of this subgraph's rewards
                indexer_share_of_subgraph = alloc / staked
                # Expected reward for this allocation
                alloc_reward = annual_issuance * subgraph_share * indexer_share_of_subgraph
                total_expected_rewards += alloc_reward
        
        # Convert stake values from wei to GRT for APR calculation
        self_stake_grt = self_stake / 1e18
        delegated_grt = delegated / 1e18
        
        # Calculate APRs
        if total_expected_rewards > 0:
            indexer_rewards = total_expected_rewards * raw_reward_cut
            delegator_rewards = total_expected_rewards * (1 - raw_reward_cut)
            
            apr_indexer = (indexer_rewards / self_stake_grt) * 100 if self_stake_grt > 0 else 0
            apr_delegators = (delegator_rewards / delegated_grt) * 100 if delegated_grt > 0 else 0
            
            print(f"  Expected rewards: {Colors.BRIGHT_CYAN}{total_expected_rewards:,.0f} GRT/year{Colors.RESET}")
            print(f"  Indexer share ({raw_reward_cut*100:.1f}%): {indexer_rewards:,.0f} GRT/year")
            print(f"  Delegator share ({(1-raw_reward_cut)*100:.1f}%): {delegator_rewards:,.0f} GRT/year")
            print(f"  APR Indexer:    {Colors.BRIGHT_GREEN}{apr_indexer:.1f}%{Colors.RESET}")
            print(f"  APR Delegators: {Colors.BRIGHT_GREEN}{apr_delegators:.2f}%{Colors.RESET}")
        else:
            print(f"  {Colors.DIM}Unable to calculate APR{Colors.RESET}")
    else:
        print(f"  {Colors.DIM}Unable to fetch network data{Colors.RESET}")
    
    # Allocation stats
    print_section("Allocations")
    active_count = indexer.get('allocationCount', 0)
    total_count = indexer.get('totalAllocationCount', 0)
    print(f"  Active: {Colors.BRIGHT_GREEN}{active_count}{Colors.RESET} | Total: {total_count}")
    
    # Get allocation history
    active_allocs, closed_allocs = client.get_indexer_allocations(indexer_id, args.hours)
    poi_submissions = client.get_indexer_poi_submissions(indexer_id, args.hours)
    recent_delegations, recent_undelegations = client.get_delegation_events(indexer_id, args.hours)
    
    # Build timeline
    events = []
    
    # Recent allocations (created in the period)
    cutoff = datetime.now() - timedelta(hours=args.hours)
    cutoff_ts = int(cutoff.timestamp())
    for alloc in active_allocs:
        created_ts = int(alloc.get('createdAt', 0))
        if datetime.fromtimestamp(created_ts) >= cutoff:
            deployment = alloc.get('subgraphDeployment', {})
            events.append({
                'type': 'allocate',
                'timestamp': created_ts,
                'tokens': alloc.get('allocatedTokens', '0'),
                'subgraph': deployment.get('ipfsHash', '?'),
                'subgraph_id': get_subgraph_id_from_deployment(deployment)
            })
    
    # Closed allocations
    for alloc in closed_allocs:
        deployment = alloc.get('subgraphDeployment', {})
        events.append({
            'type': 'unallocate',
            'timestamp': int(alloc.get('closedAt', 0)),
            'tokens': alloc.get('allocatedTokens', '0'),
            'rewards': int(alloc.get('indexingRewards', '0')),
            'subgraph': deployment.get('ipfsHash', '?'),
            'subgraph_id': get_subgraph_id_from_deployment(deployment)
        })
    
    # POI submissions (collections)
    for poi in poi_submissions:
        alloc = poi.get('allocation', {})
        if alloc.get('status') == 'Active':
            deployment = alloc.get('subgraphDeployment', {})
            events.append({
                'type': 'collect',
                'timestamp': int(poi.get('presentedAtTimestamp', 0)),
                'tokens': alloc.get('allocatedTokens', '0'),
                'rewards': int(alloc.get('indexingRewards', '0')),
                'subgraph': deployment.get('ipfsHash', '?'),
                'subgraph_id': get_subgraph_id_from_deployment(deployment)
            })
    
    # Recent delegations
    for stake in recent_delegations:
        delegator_id = stake.get('delegator', {}).get('id', '?')
        staked_tokens = stake.get('stakedTokens', '0')
        created_at = int(stake.get('createdAt') or 0)
        delegated_at = int(stake.get('lastDelegatedAt') or 0)
        # If createdAt is within the period, it's a new delegation (initial amount)
        # Otherwise it's an increase to existing delegation (total shown)
        is_new = created_at >= cutoff_ts
        events.append({
            'type': 'delegate',
            'timestamp': delegated_at,
            'tokens': staked_tokens,
            'delegator': delegator_id,
            'is_new': is_new
        })
    
    # Recent undelegations - use lockedTokens (amount in thawing) 
    for stake in recent_undelegations:
        delegator_id = stake.get('delegator', {}).get('id', '?')
        locked_tokens = stake.get('lockedTokens', '0')  # Amount being undelegated
        remaining_tokens = stake.get('stakedTokens', '0')  # Amount still delegated
        undelegated_at = int(stake.get('lastUndelegatedAt') or 0)
        events.append({
            'type': 'undelegate',
            'timestamp': undelegated_at,
            'tokens': locked_tokens,  # Show the undelegated amount
            'remaining': remaining_tokens,  # Keep track of remaining
            'delegator': delegator_id
        })
    
    if events:
        print_section(f"Activity ({args.hours}h)")
        events.sort(key=lambda x: x['timestamp'], reverse=True)
        
        for event in events[:25]:
            ts = format_timestamp(str(event['timestamp']))  # Full date + time
            tokens = format_tokens_short(event['tokens'])
            
            if event['type'] == 'allocate':
                symbol = f"{Colors.BRIGHT_GREEN}+{Colors.RESET}"
                subgraph = event.get('subgraph', '?')
                subgraph_id = event.get('subgraph_id')
                target = format_deployment_link(subgraph, subgraph_id) if subgraph != '?' else subgraph
                details = f"{tokens} GRT"
            elif event['type'] == 'unallocate':
                symbol = f"{Colors.BRIGHT_RED}-{Colors.RESET}"
                subgraph = event.get('subgraph', '?')
                subgraph_id = event.get('subgraph_id')
                target = format_deployment_link(subgraph, subgraph_id) if subgraph != '?' else subgraph
                rewards = event.get('rewards', 0) / 1e18
                rewards_str = f" → {rewards:,.0f} GRT" if rewards > 0 else ""
                details = f"{tokens} GRT{rewards_str}"
            elif event['type'] == 'collect':
                symbol = f"{Colors.BRIGHT_CYAN}${Colors.RESET}"
                subgraph = event.get('subgraph', '?')
                subgraph_id = event.get('subgraph_id')
                target = format_deployment_link(subgraph, subgraph_id) if subgraph != '?' else subgraph
                rewards = event.get('rewards', 0) / 1e18
                details = f"{rewards:,.0f} GRT collected"
            elif event['type'] == 'delegate':
                symbol = f"{Colors.BRIGHT_MAGENTA}↑{Colors.RESET}"
                target = event.get('delegator', '?')
                is_new = event.get('is_new', False)
                if is_new:
                    # New delegation - tokens is the initial amount
                    details = f"{Colors.BRIGHT_MAGENTA}+{tokens} GRT delegated{Colors.RESET}"
                else:
                    # Increase to existing - tokens is the total (we don't know how much was added)
                    details = f"{Colors.BRIGHT_MAGENTA}now {tokens} GRT (increased){Colors.RESET}"
            elif event['type'] == 'undelegate':
                symbol = f"{Colors.YELLOW}↓{Colors.RESET}"
                target = event.get('delegator', '?')
                # lockedTokens = amount in thawing period
                thawing_tokens = format_tokens_short(event['tokens'])
                remaining = int(event.get('remaining', '0')) / 1e18
                remaining_str = f"{remaining:,.0f}" if remaining >= 1 else "0"
                details = f"{Colors.YELLOW}{thawing_tokens} GRT thawing, {remaining_str} remaining{Colors.RESET}"
            else:
                continue
            
            print(f"  [{symbol}] {Colors.DIM}{ts}{Colors.RESET}  {target}  {details}")
    
    # Active allocations summary - use dedicated query for true top allocations
    top_allocs = client.get_top_allocations(indexer_id, 10)
    if top_allocs:
        print_section("Top Active Allocations")
        for alloc in top_allocs:
            deployment = alloc.get('subgraphDeployment', {})
            subgraph_hash = deployment.get('ipfsHash', '?')
            subgraph_id = get_subgraph_id_from_deployment(deployment)
            subgraph = format_deployment_link(subgraph_hash, subgraph_id) if subgraph_hash != '?' else subgraph_hash
            tokens = format_tokens(alloc.get('allocatedTokens', '0'))
            signal = int(deployment.get('signalledTokens', '0')) / 1e18
            created_ts = int(alloc.get('createdAt', 0))
            age = format_duration(int(datetime.now().timestamp()) - created_ts)
            print(f"  {subgraph}  {tokens:>12}  {Colors.DIM}{age:>8}  signal: {signal:,.0f}{Colors.RESET}")
    
    print()


if __name__ == '__main__':
    main()

