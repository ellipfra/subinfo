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
    - Config file: ~/.grtinfo/config.json (key "network_subgraph_url")
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

try:
    from web3 import Web3
    HAS_WEB3 = True
except ImportError:
    HAS_WEB3 = False

# Import shared modules
from common import (
    Colors, terminal_link, format_deployment_link,
    format_tokens, format_tokens_short, format_percentage,
    format_timestamp, format_duration, print_section
)
from config import get_network_subgraph_url, get_ens_subgraph_url, get_rpc_url
from contracts import HorizonStakingClient
from ens_client import ENSClient
from sync_status import IndexerStatusClient, format_sync_status as _format_sync_status
from logger import setup_logging, get_logger
from rewards import get_rewards_batch, calculate_reward_split

log = get_logger(__name__)


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
        self._session = requests.Session()
    
    def query(self, query: str, variables: Optional[Dict] = None) -> Dict:
        """Execute a GraphQL query"""
        try:
            response = self._session.post(
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
                delegatedThawingTokens
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
        
        # Recent closed allocations (include isLegacy field)
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
                isLegacy
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
        all_allocations = []
        skip = 0
        batch_size = 1000

        while True:
            query = f"""
            {{
                allocations(
                    where: {{ indexer: "{indexer_id.lower()}", status: Active }}
                    first: {batch_size}
                    skip: {skip}
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
            batch = result.get('allocations', [])
            if not batch:
                break
            all_allocations.extend(batch)
            if len(batch) < batch_size:
                break
            skip += batch_size

        return all_allocations
    
    def get_all_active_allocation_ids(self, indexer_id: str) -> List[str]:
        """Get all active allocation IDs for an indexer"""
        all_ids = []
        skip = 0
        batch_size = 1000

        while True:
            query = f"""
            {{
                allocations(
                    where: {{ indexer: "{indexer_id.lower()}", status: Active }}
                    first: {batch_size}
                    skip: {skip}
                ) {{
                    id
                }}
            }}
            """
            result = self.query(query)
            batch = result.get('allocations', [])
            if not batch:
                break
            all_ids.extend([a['id'] for a in batch if a.get('id')])
            if len(batch) < batch_size:
                break
            skip += batch_size

        return all_ids
    
    def get_all_active_allocations_with_created(self, indexer_id: str) -> List[Dict]:
        """Get all active allocations with their IDs and creation timestamps"""
        all_allocations = []
        skip = 0
        batch_size = 1000

        while True:
            query = f"""
            {{
                allocations(
                    where: {{ indexer: "{indexer_id.lower()}", status: Active }}
                    first: {batch_size}
                    skip: {skip}
                ) {{
                    id
                    createdAt
                    allocatedTokens
                }}
            }}
            """
            result = self.query(query)
            batch = result.get('allocations', [])
            if not batch:
                break
            all_allocations.extend(batch)
            if len(batch) < batch_size:
                break
            skip += batch_size

        return all_allocations
    
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


class LegacyRewardsClient:
    """Client to fetch legacy allocation rewards from on-chain events"""
    
    # HorizonRewardAssigned event signature
    # event HorizonRewardAssigned(address indexed indexer, address indexed allocationID, uint256 amount)
    HORIZON_REWARD_TOPIC = "0xa111914d7f2ea8beca61d12f1a1f38c5533de5f1823c3936422df4404ac2ec68"
    # RewardsManager contract on Arbitrum One
    REWARDS_MANAGER = "0x971B9d3d0Ae3ECa029CAB5eA1fB0F72c85e6a525"
    
    def __init__(self, rpc_url: str):
        if not HAS_WEB3:
            raise ImportError("web3 library is required for legacy rewards fetching")
        self.w3 = Web3(Web3.HTTPProvider(rpc_url))
    
    def get_rewards_for_allocation(self, allocation_id: str, from_block: int, to_block: int) -> int:
        """Get total rewards for a specific allocation from HorizonRewardAssigned events"""
        try:
            # Pad allocation ID to 32 bytes for topic filter
            alloc_topic = "0x" + allocation_id.lower()[2:].zfill(64)
            
            logs = self.w3.eth.get_logs({
                "address": self.REWARDS_MANAGER,
                "topics": [
                    self.HORIZON_REWARD_TOPIC,
                    None,  # indexer (any)
                    alloc_topic  # allocation ID
                ],
                "fromBlock": from_block,
                "toBlock": to_block
            })
            
            total_rewards = 0
            for log in logs:
                # Data contains the amount (uint256)
                amount = int(log.data.hex(), 16)
                total_rewards += amount
            
            return total_rewards
        except Exception as e:
            return 0
    
    def get_rewards_for_allocations(self, allocations: List[Dict], indexer_id: str) -> Dict[str, int]:
        """Get rewards for multiple allocations efficiently using batch requests"""
        if not allocations:
            return {}
        
        rewards_map = {}
        
        # Get current block for the "to" block
        try:
            current_block = self.w3.eth.block_number
        except:
            return rewards_map
        
        # Pad indexer ID for topic filter
        indexer_topic = "0x" + indexer_id.lower()[2:].zfill(64)
        
        # Find the earliest creation block among allocations
        earliest_created = min(int(a.get('createdAt', 0)) for a in allocations)
        # Convert timestamp to approximate block (Arbitrum: ~0.25s per block)
        # Go back a bit further to be safe
        from_block = max(0, current_block - int((time.time() - earliest_created) / 0.25) - 10000)
        
        try:
            # Get all HorizonRewardAssigned events for this indexer
            logs = self.w3.eth.get_logs({
                "address": self.REWARDS_MANAGER,
                "topics": [
                    self.HORIZON_REWARD_TOPIC,
                    indexer_topic  # indexer
                ],
                "fromBlock": from_block,
                "toBlock": current_block
            })
            
            # Parse logs and map to allocations
            for log in logs:
                allocation_id = "0x" + log.topics[2].hex()[-40:]
                amount = int(log.data.hex(), 16)
                
                if allocation_id not in rewards_map:
                    rewards_map[allocation_id] = 0
                rewards_map[allocation_id] += amount
            
        except Exception as e:
            pass
        
        return rewards_map


def format_sync_status(status: Optional[Dict]) -> str:
    """Format sync status as a colored indicator (wrapper using local Colors)"""
    return _format_sync_status(status, Colors)


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
    parser.add_argument(
        '-r', '--rewards',
        action='store_true',
        help='Calculate total accrued rewards from all allocations (requires RPC)'
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
    delegated_thawing = int(indexer.get('delegatedThawingTokens', '0'))
    allocated = int(indexer.get('allocatedTokens', '0'))
    available_stake = int(indexer.get('availableStake', '0'))
    token_capacity = int(indexer.get('tokenCapacity', '0'))

    # Workaround: fetch accurate tokenCapacity from contract
    # The subgraph's tokenCapacity can be stale due to delegationExchangeRate not being updated
    # See: https://github.com/graphprotocol/graph-network-subgraph/issues/323
    rpc_url = get_rpc_url()
    if rpc_url:
        staking_client = HorizonStakingClient(rpc_url)
        contract_capacity = staking_client.get_tokens_available(indexer_id)
        if contract_capacity is not None and contract_capacity != token_capacity:
            log.debug(f"Using contract tokenCapacity ({contract_capacity}) instead of subgraph ({token_capacity})")
            token_capacity = contract_capacity

    # Delegations in thawing = delegated - delegatedCapacity
    delegations_thawing = delegated - delegated_capacity

    # token_capacity already includes thawing delegations (from contract or subgraph)
    # No need to add delegated_thawing again - that would be double-counting
    total_stake = token_capacity if token_capacity > 0 else (self_stake + delegated)
    # Calculate remaining directly (can be negative if over-allocated)
    remaining = total_stake - allocated
    remaining_pct = (remaining / total_stake * 100) if total_stake > 0 else 0
    
    # Delegation capacity (16x multiplier is the protocol default)
    delegation_ratio = 16
    max_delegation = self_stake * delegation_ratio
    # Tokens in thawing still occupy delegation slots until fully withdrawn
    delegation_remaining = max(0, max_delegation - delegated)
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
    
    # Accrued rewards (on-chain) - only if --rewards flag is set
    if args.rewards:
        rpc_url = get_rpc_url()
        if not rpc_url:
            print_section("Accrued Rewards")
            print(f"  {Colors.DIM}RPC URL not configured. Set RPC_URL or add rpc_url to config.{Colors.RESET}")
        elif not HAS_WEB3:
            print_section("Accrued Rewards")
            print(f"  {Colors.DIM}web3 library not installed. Run: pip install web3{Colors.RESET}")
        else:
            # Get allocations with creation timestamps
            allocations_with_created = client.get_all_active_allocations_with_created(indexer_id)
            if allocations_with_created:
                allocation_ids = [a['id'] for a in allocations_with_created if a.get('id')]
                
                print_section(f"Accrued Rewards ({len(allocation_ids)} allocations)")
                print(f"  {Colors.DIM}Fetching rewards from smart contract...{Colors.RESET}", end='', flush=True)
                
                rewards_map = get_rewards_batch(allocation_ids, rpc_url, max_workers=5)
                
                # Calculate totals
                total_rewards = sum(r for r in rewards_map.values() if r is not None and r > 0)
                successful = sum(1 for r in rewards_map.values() if r is not None)
                failed = len(allocation_ids) - successful
                
                # Clear the "Fetching..." line
                print(f"\r{' ' * 60}\r", end='')
                
                if total_rewards > 0:
                    split = calculate_reward_split(total_rewards, raw_reward_cut)
                    print(f"  Total accrued:     {Colors.BRIGHT_CYAN}{total_rewards:,.0f} GRT{Colors.RESET}")
                    print(f"  Indexer share:     {Colors.BRIGHT_GREEN}{split['indexer']:,.0f} GRT{Colors.RESET} ({raw_reward_cut*100:.1f}%)")
                    print(f"  Delegator share:   {Colors.DIM}{split['delegators']:,.0f} GRT{Colors.RESET} ({(1-raw_reward_cut)*100:.1f}%)")
                    if failed > 0:
                        print(f"  {Colors.DIM}⚠ {failed} allocations failed to fetch{Colors.RESET}")
                    
                    # Build histogram by epochs until expiration
                    from contracts import EPOCH_DURATION_SECONDS, MAX_ALLOCATION_EPOCHS
                    now = datetime.now().timestamp()
                    
                    # Group rewards by epochs remaining until expiration
                    epoch_buckets = {}  # epoch_remaining -> (total_rewards, count)
                    
                    for alloc in allocations_with_created:
                        alloc_id = alloc.get('id', '').lower()
                        created_at = int(alloc.get('createdAt', 0))
                        reward = rewards_map.get(alloc_id) or rewards_map.get(alloc_id.lower()) or 0
                        
                        if created_at > 0 and reward and reward > 0:
                            # Calculate age in epochs (days)
                            age_seconds = now - created_at
                            age_epochs = int(age_seconds / EPOCH_DURATION_SECONDS)
                            # Can be negative if allocation is past max age
                            epochs_remaining = MAX_ALLOCATION_EPOCHS - age_epochs
                            
                            if epochs_remaining not in epoch_buckets:
                                epoch_buckets[epochs_remaining] = {'rewards': 0, 'count': 0}
                            epoch_buckets[epochs_remaining]['rewards'] += reward
                            epoch_buckets[epochs_remaining]['count'] += 1
                    
                    # Display histogram
                    if epoch_buckets:
                        print(f"\n  {Colors.BOLD}Rewards by epochs until expiration:{Colors.RESET}")
                        print(f"  {Colors.DIM}(allocations expire after 28 epochs ≈ 28 days){Colors.RESET}")
                        
                        max_reward = max(b['rewards'] for b in epoch_buckets.values()) if epoch_buckets else 0
                        bar_width = 30
                        
                        # Group epochs into buckets: expired (<0), 0, 1-3, 4-7, 8-14, 15-21, 22-28
                        # Note: negative epochs means allocation is past max age (should have been closed)
                        # Use -9999 to catch all expired allocations regardless of how old
                        bucket_ranges = [(-9999, -1), (0, 0), (1, 3), (4, 7), (8, 14), (15, 21), (22, 28)]
                        bucket_labels = ["exp!", "0d", "1-3d", "4-7d", "8-14d", "15-21d", "22-28d"]
                        
                        for (start, end), label_text in zip(bucket_ranges, bucket_labels):
                            # For expired bucket, sum all negative epochs
                            if start < -100:
                                bucket_rewards = sum(v['rewards'] for k, v in epoch_buckets.items() if k < 0)
                                bucket_count = sum(v['count'] for k, v in epoch_buckets.items() if k < 0)
                            else:
                                bucket_rewards = sum(epoch_buckets.get(e, {}).get('rewards', 0) for e in range(start, end + 1))
                                bucket_count = sum(epoch_buckets.get(e, {}).get('count', 0) for e in range(start, end + 1))
                            
                            # Color based on urgency
                            if end <= 0:
                                color = Colors.BRIGHT_RED  # Expired or expiring today - CRITICAL
                                prefix = "⚠️ "  # emoji (2 visual cells) + space = 3 visual cells
                            elif start <= 3:
                                color = Colors.BRIGHT_YELLOW  # 1-3 days - soon
                                prefix = "⏰ "  # emoji (2 visual cells) + space = 3 visual cells
                            else:
                                color = Colors.BRIGHT_GREEN  # Safe
                                prefix = "   "  # 3 spaces to match emoji + space
                            
                            bar_len = min(bar_width, int((bucket_rewards / max_reward) * bar_width)) if max_reward > 0 and bucket_rewards > 0 else 0
                            bar = '█' * bar_len + '░' * (bar_width - bar_len)
                            
                            # Consistent formatting: prefix (3 cells) + label (6 chars right-aligned) + bar
                            if bucket_rewards > 0:
                                print(f"  {prefix}{label_text:>6} {color}{bar}{Colors.RESET} {bucket_rewards:>10,.0f} GRT ({bucket_count:>3})")
                            else:
                                print(f"  {prefix}{label_text:>6} {Colors.DIM}{bar}{Colors.RESET}          - GRT")
                else:
                    print(f"  {Colors.DIM}No accrued rewards found (all allocations may be newly opened){Colors.RESET}")
            else:
                print_section("Accrued Rewards")
                print(f"  {Colors.DIM}No active allocations found{Colors.RESET}")
    
    # Allocation stats
    print_section("Allocations")
    active_count = indexer.get('allocationCount', 0)
    total_count = indexer.get('totalAllocationCount', 0)
    print(f"  Active: {Colors.BRIGHT_GREEN}{active_count}{Colors.RESET} | Total: {total_count}")
    
    # Get allocation history
    active_allocs, closed_allocs = client.get_indexer_allocations(indexer_id, args.hours)
    poi_submissions = client.get_indexer_poi_submissions(indexer_id, args.hours)
    recent_delegations, recent_undelegations = client.get_delegation_events(indexer_id, args.hours)
    
    # Enrich legacy allocation rewards from on-chain events if RPC is available
    legacy_rewards_map = {}
    rpc_url = get_rpc_url()
    if rpc_url and HAS_WEB3:
        legacy_allocs = [a for a in closed_allocs if a.get('isLegacy') and int(a.get('indexingRewards', '0')) == 0]
        if legacy_allocs:
            try:
                legacy_client = LegacyRewardsClient(rpc_url)
                legacy_rewards_map = legacy_client.get_rewards_for_allocations(legacy_allocs, indexer_id)
            except Exception:
                pass
    
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
        # Use on-chain rewards for legacy allocations if available
        rewards = int(alloc.get('indexingRewards', '0'))
        alloc_id = alloc.get('id', '').lower()
        if alloc.get('isLegacy') and rewards == 0 and alloc_id in legacy_rewards_map:
            rewards = legacy_rewards_map[alloc_id]
        events.append({
            'type': 'unallocate',
            'timestamp': int(alloc.get('closedAt', 0)),
            'tokens': alloc.get('allocatedTokens', '0'),
            'rewards': rewards,
            'subgraph': deployment.get('ipfsHash', '?'),
            'subgraph_id': get_subgraph_id_from_deployment(deployment),
            'is_legacy': alloc.get('isLegacy', False)
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
    
    # Separate allocation events from delegation events
    allocation_events = [e for e in events if e['type'] in ('allocate', 'unallocate', 'collect')]
    delegation_events = [e for e in events if e['type'] in ('delegate', 'undelegate')]

    if allocation_events:
        print_section(f"Allocation Activity ({args.hours}h)")
        allocation_events.sort(key=lambda x: x['timestamp'], reverse=True)

        for event in allocation_events[:20]:
            ts = format_timestamp(str(event['timestamp']))
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
                is_legacy = event.get('is_legacy', False)
                legacy_marker = f" {Colors.DIM}(legacy){Colors.RESET}" if is_legacy else ""
                rewards_str = f" → {rewards:,.0f} GRT{legacy_marker}" if rewards > 0 else ""
                details = f"{tokens} GRT{rewards_str}"
            elif event['type'] == 'collect':
                symbol = f"{Colors.BRIGHT_CYAN}${Colors.RESET}"
                subgraph = event.get('subgraph', '?')
                subgraph_id = event.get('subgraph_id')
                target = format_deployment_link(subgraph, subgraph_id) if subgraph != '?' else subgraph
                rewards = event.get('rewards', 0) / 1e18
                details = f"{rewards:,.0f} GRT collected"
            else:
                continue

            print(f"  [{symbol}] {Colors.DIM}{ts}{Colors.RESET}  {target}  {details}")

    if delegation_events:
        print_section(f"Delegation Activity ({args.hours}h)")
        delegation_events.sort(key=lambda x: x['timestamp'], reverse=True)

        for event in delegation_events[:15]:
            ts = format_timestamp(str(event['timestamp']))
            tokens = format_tokens_short(event['tokens'])

            if event['type'] == 'delegate':
                symbol = f"{Colors.BRIGHT_MAGENTA}↑{Colors.RESET}"
                target = event.get('delegator', '?')
                is_new = event.get('is_new', False)
                if is_new:
                    details = f"{Colors.BRIGHT_MAGENTA}+{tokens} GRT delegated{Colors.RESET}"
                else:
                    details = f"{Colors.BRIGHT_MAGENTA}now {tokens} GRT (increased){Colors.RESET}"
            elif event['type'] == 'undelegate':
                symbol = f"{Colors.YELLOW}↓{Colors.RESET}"
                target = event.get('delegator', '?')
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
        # Get sync status from indexer's public status endpoint
        indexer_url = indexer.get('url')
        sync_statuses = {}
        status_error = None
        status_client = None
        
        if indexer_url:
            status_client = IndexerStatusClient(timeout=15)
            sync_statuses = status_client.get_all_deployments_status(indexer_url)
            if not sync_statuses and status_client.last_error:
                status_error = status_client.last_error
        else:
            status_error = "No indexer URL in network subgraph"
        
        print_section("Top Active Allocations")
        if status_error:
            print(f"  {Colors.DIM}⚠ Sync status unavailable: {status_error}{Colors.RESET}")
        for alloc in top_allocs:
            deployment = alloc.get('subgraphDeployment', {})
            subgraph_hash = deployment.get('ipfsHash', '?')
            subgraph_id = get_subgraph_id_from_deployment(deployment)
            subgraph = format_deployment_link(subgraph_hash, subgraph_id) if subgraph_hash != '?' else subgraph_hash
            tokens = format_tokens(alloc.get('allocatedTokens', '0'))
            signal = int(deployment.get('signalledTokens', '0')) / 1e18
            created_ts = int(alloc.get('createdAt', 0))
            age = format_duration(int(datetime.now().timestamp()) - created_ts)
            
            # Get sync status for this deployment
            sync_status = sync_statuses.get(subgraph_hash)
            sync_indicator = format_sync_status(sync_status) if sync_statuses else ""
            
            if sync_indicator:
                print(f"  {subgraph}  {tokens:>12}  {Colors.DIM}{age:>8}{Colors.RESET}  {sync_indicator}")
            else:
                print(f"  {subgraph}  {tokens:>12}  {Colors.DIM}{age:>8}  signal: {signal:,.0f}{Colors.RESET}")
    
    print()


if __name__ == '__main__':
    main()

