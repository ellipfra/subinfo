#!/usr/bin/env python3
"""
delegatorinfo - Display delegator information from The Graph Network

Usage:
    delegatorinfo <delegator_address_or_ens>
    
Search by:
    - ENS name (e.g., "delegator.eth")
    - Full address (e.g., "0x1234...")
    - Partial address (e.g., "0x1234")

Configuration:
    - Environment variable: THEGRAPH_NETWORK_SUBGRAPH_URL
    - Config file: ~/.grtinfo/config.json (key "network_subgraph_url")
    - Analytics subgraph: THEGRAPH_ANALYTICS_SUBGRAPH_URL or config "analytics_subgraph_url"
    - RPC URL: Environment variable ARBITRUM_RPC_URL or RPC_URL, or config key "rpc_url"
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
from functools import lru_cache
from concurrent.futures import ThreadPoolExecutor, as_completed

# Import shared modules
from common import (
    Colors, terminal_link,
    format_tokens, format_tokens_short, format_duration, print_section
)
from config import get_network_subgraph_url, get_ens_subgraph_url, get_analytics_subgraph_url
from ens import ENSClient
from contracts import REWARDS_MANAGER, STAKING, SUBGRAPH_SERVICE, GRT_DECIMALS
from logger import setup_logging, get_logger

log = get_logger(__name__)


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
    
    def get_delegator_delegations(self, delegator_id: str) -> List[Dict]:
        """Get all delegations for a delegator (including active and thawing)"""
        # Use f-string approach like indexerinfo.py
        delegator_lower = delegator_id.lower()
        # Use variables approach to avoid f-string escaping issues
        query = """
        query GetDelegatorDelegations($delegator: String!) {
            delegatedStakes(
                where: { delegator: $delegator }
                orderBy: createdAt
                orderDirection: desc
                first: 1000
            ) {
                id
                indexer {
                    id
                    url
                    delegatedTokens
                    delegatorShares
                }
                stakedTokens
                shareAmount
                lockedTokens
                createdAt
                lastDelegatedAt
                lastUndelegatedAt
            }
        }
        """
        result = self.query(query, {'delegator': delegator_lower})
        delegations = result.get('delegatedStakes', [])
        if not delegations:
            return []
        
        # Filter to only include delegations with stakedTokens > 0 OR lockedTokens > 0
        # Handle both string and int values
        filtered = []
        for d in delegations:
            staked_str = d.get('stakedTokens', '0')
            locked_str = d.get('lockedTokens', '0')
            try:
                staked = int(staked_str) if isinstance(staked_str, str) else staked_str
                locked = int(locked_str) if isinstance(locked_str, str) else locked_str
            except (ValueError, TypeError):
                staked = 0
                locked = 0
            
            if staked > 0 or locked > 0:
                filtered.append(d)
        
        # Sort by stakedTokens (desc) for display, but keep all entries
        filtered.sort(key=lambda x: int(x.get('stakedTokens', '0')), reverse=True)
        return filtered
    
    def get_delegator_allocations(self, delegator_id: str, active_only: bool = True, indexer_ids: Optional[List[str]] = None) -> List[Dict]:
        """Get allocations where this delegator has staked tokens
        If indexer_ids is provided, use those directly instead of querying delegations"""
        if indexer_ids is None:
            # Get delegations first - only active ones if requested
            delegations = self.get_delegator_delegations(delegator_id)
            
            # Filter to only active delegations if requested
            if active_only:
                # Group by indexer to detect stale entries
                indexer_delegations = {}
                for d in delegations:
                    indexer_id = d.get('indexer', {}).get('id')
                    if indexer_id:
                        if indexer_id not in indexer_delegations:
                            indexer_delegations[indexer_id] = []
                        indexer_delegations[indexer_id].append(d)
                
                # Helper function to check if delegation is active
                def is_active_delegation(d, all_indexer_delegations):
                    indexer_id = d.get('indexer', {}).get('id')
                    if not indexer_id:
                        return False
                    staked = int(d.get('stakedTokens', '0'))
                    locked = int(d.get('lockedTokens', '0'))
                    last_undelegated = d.get('lastUndelegatedAt')
                    if staked == 0 or last_undelegated is not None:
                        return False
                    indexer_dels = all_indexer_delegations.get(indexer_id, [])
                    has_recent_undelegation = any(int(del_item.get('lockedTokens', '0')) > 0 for del_item in indexer_dels)
                    return not has_recent_undelegation
                
                delegations = [d for d in delegations if is_active_delegation(d, indexer_delegations)]
            
            indexer_ids = [d['indexer']['id'] for d in delegations if d.get('indexer')]
        
        if not indexer_ids:
            return []
        
        # Get active allocations for these indexers
        query = """
        query GetDelegatorAllocations($indexers: [String!]!) {
            allocations(
                where: { indexer_in: $indexers, status: Active }
                orderBy: createdAt
                orderDirection: desc
            ) {
                id
                indexer {
                    id
                }
                allocatedTokens
                createdAt
                subgraphDeployment {
                    ipfsHash
                    signalledTokens
                }
            }
        }
        """
        result = self.query(query, {'indexers': indexer_ids})
        return result.get('allocations', [])
    
    def get_indexer_details(self, indexer_id: str) -> Optional[Dict]:
        """Get indexer details"""
        query = """
        query GetIndexer($id: String!) {
            indexer(id: $id) {
                id
                url
                stakedTokens
                delegatedTokens
                delegatorRewards
                rewardCut
                queryFeeCut
            }
        }
        """
        result = self.query(query, {'id': indexer_id.lower()})
        return result.get('indexer')


class AnalyticsClient:
    """Client to query The Graph Analytics subgraph"""
    
    def __init__(self, analytics_subgraph_url: str):
        self.analytics_subgraph_url = analytics_subgraph_url.rstrip('/')
        self._session = requests.Session()
    
    def query(self, query: str, variables: Optional[Dict] = None) -> Dict:
        """Execute a GraphQL query"""
        try:
            response = self._session.post(
                self.analytics_subgraph_url,
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
            print(f"{Colors.DIM}Analytics query error: {e}{Colors.RESET}", file=sys.stderr)
            return {}
    
    def get_delegator_stats(self, delegator_id: str) -> Optional[Dict]:
        """Get delegator statistics from analytics subgraph"""
        query = """
        query GetDelegatorStats($delegator: String!) {
            delegator(id: $delegator) {
                id
                totalStakedTokens
                totalRealizedRewards
                totalUnrealizedRewards
                totalUnstakedTokens
                stakes(first: 1000, orderBy: stakedTokens, orderDirection: desc) {
                    id
                    indexer {
                        id
                    }
                    stakedTokens
                    lockedTokens
                    realizedRewards
                    unrealizedRewards
                }
            }
        }
        """
        result = self.query(query, {'delegator': delegator_id.lower()})
        return result.get('delegator')


def format_timestamp(ts: str) -> str:
    try:
        dt = datetime.fromtimestamp(int(ts))
        return dt.strftime('%Y-%m-%d %H:%M')
    except:
        return 'Unknown'


def get_rpc_url() -> str:
    """Get RPC URL from environment variable or config file
    Priority: Environment variable > Config file > Default"""
    # Priority 1: Environment variable
    env_rpc = os.environ.get('ARBITRUM_RPC_URL') or os.environ.get('RPC_URL')
    if env_rpc:
        return env_rpc.rstrip('/')
    
    # Priority 2: Config file
    config_file = Path.home() / '.grtinfo' / 'config.json'
    if config_file.exists():
        try:
            with open(config_file, 'r') as f:
                content = f.read().strip()
                if content:
                    config = json.loads(content)
                    rpc_url = config.get('rpc_url') or config.get('arbitrum_rpc_url')
                    if rpc_url:
                        return rpc_url.rstrip('/')
        except:
            pass
    
    # Priority 3: Default public RPC
    return "https://arb1.arbitrum.io/rpc"


# Cache for accrued rewards (allocation_id -> rewards)
_accrued_rewards_cache: Dict[str, Optional[float]] = {}
_cache_file = Path.home() / '.grtinfo' / 'accrued_rewards_cache.json'
_cache_ttl = 3600  # 1 hour cache TTL


def _load_accrued_rewards_cache():
    """Load accrued rewards cache from disk"""
    global _accrued_rewards_cache
    if _cache_file.exists():
        try:
            with open(_cache_file, 'r') as f:
                cache_data = json.loads(f.read())
                now = time.time()
                for alloc_id, entry in cache_data.items():
                    if isinstance(entry, dict) and 'rewards' in entry and 'timestamp' in entry:
                        # Check if cache entry is still valid
                        if now - entry['timestamp'] < _cache_ttl:
                            _accrued_rewards_cache[alloc_id] = entry['rewards']
        except:
            pass


def _save_accrued_rewards_cache():
    """Save accrued rewards cache to disk"""
    try:
        cache_data = {}
        now = time.time()
        for alloc_id, rewards in _accrued_rewards_cache.items():
            cache_data[alloc_id] = {
                'rewards': rewards,
                'timestamp': now
            }
        _cache_file.parent.mkdir(parents=True, exist_ok=True)
        with open(_cache_file, 'w') as f:
            json.dump(cache_data, f)
    except:
        pass


# Load cache on module import
_load_accrued_rewards_cache()


def get_accrued_rewards_from_contract(allocation_id: str, rpc_url: Optional[str] = None, use_cache: bool = True) -> Optional[float]:
    """Get exact accrued rewards from the RewardsManager smart contract
    Checks both pre-Horizon (Staking) and Horizon (SubgraphService) rewards issuers
    and sums them up, as an allocation can have rewards from both systems
    
    Args:
        allocation_id: The allocation ID to check
        rpc_url: Optional RPC URL. If not provided, uses configured RPC from config file or environment variable
        use_cache: Whether to use cache (default: True)
    """
    # Check cache first
    if use_cache and allocation_id in _accrued_rewards_cache:
        return _accrued_rewards_cache[allocation_id]
    
    try:
        from web3 import Web3
        
        # Use provided RPC URL or get from config
        if rpc_url is None:
            rpc_url = get_rpc_url()
        
        w3 = Web3(Web3.HTTPProvider(rpc_url))
        
        # Contract addresses imported from contracts.py
        # Function selector for getRewards(address,address)
        selector = Web3.keccak(text="getRewards(address,address)")[:4].hex()
        
        total_rewards = 0.0
        
        # Sum rewards from both issuers (pre-Horizon and Horizon)
        # An allocation can have rewards from both systems
        for issuer in [STAKING, SUBGRAPH_SERVICE]:
            try:
                calldata = selector + issuer[2:].lower().zfill(64) + allocation_id[2:].lower().zfill(64)
                result = w3.eth.call({
                    "to": Web3.to_checksum_address(REWARDS_MANAGER),
                    "data": f"0x{calldata}"
                })
                rewards_wei = int(result.hex(), 16)
                if rewards_wei > 0:
                    total_rewards += rewards_wei / (10 ** GRT_DECIMALS)
            except:
                continue
        
        result = total_rewards if total_rewards > 0 else None
        
        # Cache the result
        if use_cache:
            _accrued_rewards_cache[allocation_id] = result
        
        return result
    except ImportError:
        return None
    except Exception:
        return None


def get_accrued_rewards_batch(allocation_ids: List[str], rpc_url: Optional[str] = None, max_workers: int = 10) -> Dict[str, Optional[float]]:
    """Get accrued rewards for multiple allocations in parallel
    
    Args:
        allocation_ids: List of allocation IDs to check
        rpc_url: Optional RPC URL
        max_workers: Maximum number of parallel workers (default: 10)
    
    Returns:
        Dictionary mapping allocation_id -> rewards
    """
    results = {}
    
    # Filter out already cached allocations
    uncached_ids = [aid for aid in allocation_ids if aid not in _accrued_rewards_cache]
    cached_ids = [aid for aid in allocation_ids if aid in _accrued_rewards_cache]
    
    # Add cached results
    for aid in cached_ids:
        results[aid] = _accrued_rewards_cache[aid]
    
    if not uncached_ids:
        return results
    
    # Fetch uncached allocations in parallel
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_id = {
            executor.submit(get_accrued_rewards_from_contract, aid, rpc_url, use_cache=False): aid
            for aid in uncached_ids
        }
        
        for future in as_completed(future_to_id):
            allocation_id = future_to_id[future]
            try:
                results[allocation_id] = future.result()
            except Exception:
                results[allocation_id] = None
    
    # Save cache after batch operation
    _save_accrued_rewards_cache()
    
    return results


def get_delegator_total_balance_from_staking(delegator_id: str, indexer_id: str, rpc_url: Optional[str] = None) -> Optional[float]:
    """Get total balance (including accrued rewards) for a delegator from a specific indexer
    This calculates: (delegationPool.tokens * delegatorShares / delegationPool.shares)
    where delegationPool.tokens includes all accumulated rewards
    
    Uses the Horizon Staking contract's delegation pool to calculate the total balance.
    
    Args:
        delegator_id: The delegator address
        indexer_id: The indexer address (serviceProvider)
        rpc_url: Optional RPC URL
    
    Returns:
        Total balance in GRT (including rewards), or None if failed
    """
    try:
        from web3 import Web3
        from eth_abi import encode, decode
        
        if rpc_url is None:
            rpc_url = get_rpc_url()
        
        w3 = Web3(Web3.HTTPProvider(rpc_url))
        
        # Contract address on Arbitrum One (Horizon Staking)
        STAKING = "0x00669A4CF01450B64E8A2A20E9b1FCB71E61eF03"
        
        # In Horizon, we need serviceProvider and verifier
        # The verifier is always the SubgraphService contract
        SUBGRAPH_SERVICE = "0xb2Bb92d0DE618878E438b55D5846cfecD9301105"  # SubgraphService (verifier)
        service_provider = Web3.to_checksum_address(indexer_id)
        verifier = Web3.to_checksum_address(SUBGRAPH_SERVICE)
        
        try:
            # 1. Get delegator's shares: getDelegation(address serviceProvider, address verifier, address delegator)
            # Returns: Delegation struct with (uint256 shares)
            selector_get_delegation = Web3.keccak(text="getDelegation(address,address,address)")[:4]
            encoded_params_delegation = encode(
                ['address', 'address', 'address'],
                [service_provider, verifier, Web3.to_checksum_address(delegator_id)]
            )
            calldata_delegation = selector_get_delegation + encoded_params_delegation
            
            result_delegation = w3.eth.call({
                "to": Web3.to_checksum_address(STAKING),
                "data": calldata_delegation
            })
            
            # Decode result: Delegation struct with (uint256 shares)
            delegator_shares = decode(['uint256'], result_delegation)[0]
            
            if delegator_shares == 0:
                # Try with verifier = zero address (some delegations might use zero verifier)
                # Or this might be a legacy delegation not in Horizon
                return None  # No delegation found with this verifier
            
            # 2. Get delegation pool: getDelegationPool(address serviceProvider, address verifier)
            # Returns: DelegationPool struct with (uint256 tokens, uint256 shares, uint256 tokensThawing, uint256 sharesThawing, uint256 thawingNonce)
            selector_get_pool = Web3.keccak(text="getDelegationPool(address,address)")[:4]
            encoded_params_pool = encode(
                ['address', 'address'],
                [service_provider, verifier]
            )
            calldata_pool = selector_get_pool + encoded_params_pool
            
            result_pool = w3.eth.call({
                "to": Web3.to_checksum_address(STAKING),
                "data": calldata_pool
            })
            
            # Decode result: DelegationPool struct
            decoded_pool = decode(['uint256', 'uint256', 'uint256', 'uint256', 'uint256'], result_pool)
            pool_tokens = decoded_pool[0]  # Total tokens in pool (includes rewards)
            pool_shares = decoded_pool[1]  # Total shares in pool
            
            if pool_shares == 0:
                return None
            
            # Calculate total balance: (pool_tokens * delegator_shares / pool_shares) / 1e18
            # This gives the total balance including all accrued rewards
            total_balance_wei = (pool_tokens * delegator_shares) // pool_shares
            total_balance = total_balance_wei / 1e18
            
            return total_balance
            
        except Exception as e:
            # If contract calls fail, return None to fall back to allocation-based calculation
            return None
        
    except ImportError:
        return None
    except Exception:
        return None


def get_delegator_total_rewards_from_contract(delegator_id: str, indexer_id: str, rpc_url: Optional[str] = None) -> Optional[float]:
    """Get total accrued rewards for a delegator from a specific indexer by summing all allocation rewards
    This includes rewards from both active and closed allocations
    
    Args:
        delegator_id: The delegator address  
        indexer_id: The indexer address
        rpc_url: Optional RPC URL
    
    Returns:
        Total rewards in GRT, or None if failed
    """
    try:
        from web3 import Web3
        
        if rpc_url is None:
            rpc_url = get_rpc_url()
        
        w3 = Web3(Web3.HTTPProvider(rpc_url))
        
        # Contract addresses imported from contracts.py
        # This function needs allocation IDs from subgraph to work
        # The logic would sum rewards from all allocations for this indexer
        
        return None  # This approach needs allocation IDs from subgraph
    except ImportError:
        return None
    except Exception:
        return None


def get_indexer_reward_cut(indexer_id: str, network_url: str) -> Optional[float]:
    """Get indexer reward cut percentage"""
    try:
        client = TheGraphClient(network_url)
        indexer = client.get_indexer_details(indexer_id)
        if indexer:
            reward_cut = indexer.get('rewardCut')
            if reward_cut is not None:
                return int(reward_cut) / 1e6  # Convert from PPM to decimal
    except:
        pass
    return None


def main():
    parser = argparse.ArgumentParser(
        description='Display delegator information from The Graph Network',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    delegatorinfo 0x1234...           # Search by address
    delegatorinfo delegator.eth        # Search by ENS name
        """
    )
    parser.add_argument('delegator', help='Delegator address or ENS name')
    parser.add_argument('--hours', type=int, default=48, help='Hours of history to show (default: 48)')
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
    
    # Analytics subgraph URL
    # Priority: 1. Environment variable, 2. Config file
    analytics_url = os.environ.get('THEGRAPH_ANALYTICS_SUBGRAPH_URL')
    if not analytics_url:
        config_file = Path.home() / '.grtinfo' / 'config.json'
        if config_file.exists():
            try:
                with open(config_file) as f:
                    config = json.load(f)
                    analytics_url = config.get('analytics_subgraph_url')
            except Exception:
                pass
    
    ens_url = get_ens_subgraph_url()
    
    client = TheGraphClient(network_url)
    analytics_client = AnalyticsClient(analytics_url) if analytics_url else None
    ens_client = ENSClient(ens_url) if ens_url else None
    
    # Resolve delegator address
    delegator_id = args.delegator
    if not delegator_id.startswith('0x'):
        # Try to resolve ENS name
        if ens_client:
            resolved = ens_client.resolve_name(delegator_id)
            if resolved:
                delegator_id = resolved
            else:
                print(f"{Colors.RED}Could not resolve ENS name '{args.delegator}'{Colors.RESET}", file=sys.stderr)
                sys.exit(1)
        else:
            print(f"{Colors.RED}Invalid address format. ENS resolution not available.{Colors.RESET}", file=sys.stderr)
            sys.exit(1)
    
    # Normalize address
    delegator_id = delegator_id.lower()
    
    # Resolve ENS name for display
    ens_name = ens_client.resolve_address(delegator_id) if ens_client else None
    
    # Display header
    print(f"{Colors.BOLD}Delegator:{Colors.RESET} ", end='')
    if ens_name:
        print(f"{Colors.BRIGHT_CYAN}{ens_name}{Colors.RESET} ({delegator_id})")
    else:
        print(f"{Colors.CYAN}{delegator_id}{Colors.RESET}")
    
    # Get delegations
    delegations = client.get_delegator_delegations(delegator_id)
    
    if not delegations:
        print(f"\n{Colors.DIM}No delegations found.{Colors.RESET}")
        sys.exit(0)
    
    # Group delegations by indexer to detect stale entries
    indexer_delegations = {}
    for d in delegations:
        indexer_id = d.get('indexer', {}).get('id')
        if indexer_id:
            if indexer_id not in indexer_delegations:
                indexer_delegations[indexer_id] = []
            indexer_delegations[indexer_id].append(d)
    
    # Helper function to check if a delegation is truly active
    def is_active_delegation(d, all_indexer_delegations):
        """Check if a delegation is truly active (not stale)"""
        indexer_id = d.get('indexer', {}).get('id')
        if not indexer_id:
            return False
        
        staked = int(d.get('stakedTokens', '0'))
        locked = int(d.get('lockedTokens', '0'))
        last_undelegated = d.get('lastUndelegatedAt')
        shares = int(d.get('shareAmount', '0'))
        
        # A delegation is active if:
        # 1. It has staked tokens > 0
        # 2. It has never been undelegated (lastUndelegatedAt is None)
        # 3. It doesn't have locked tokens (lockedTokens == 0)
        # 4. It has positive shares (shareAmount > 0) - important for detecting cancelled delegations
        if staked == 0 or last_undelegated is not None or locked > 0:
            return False
        
        # Check shares - if 0 or negative, the delegation is not truly active
        # This happens when there's a matching thawing entry that cancels the shares
        if shares <= 0:
            return False
        
        return True
    
    # Calculate totals - will be recalculated after getting analytics data if available
    total_staked = sum(
        int(d.get('stakedTokens', '0'))
        for d in delegations
        if is_active_delegation(d, indexer_delegations)
    )
    total_locked = sum(int(d.get('lockedTokens', '0')) for d in delegations)
    
    # Get rewards from analytics subgraph if available
    total_rewards = 0
    total_query_fees = 0
    total_unrealized_rewards = 0
    total_unstaked = 0  # Tokens that have been withdrawn
    indexer_rewards_map = {}  # Always initialize
    indexer_unrealized_map = {}  # Map of indexer_id -> unrealizedRewards (always initialize)
    
    if analytics_client:
        analytics_stats = analytics_client.get_delegator_stats(delegator_id)
        if analytics_stats:
            # Get total realized rewards from analytics subgraph
            # Note: totalRealizedRewards appears to be in wei but returned as decimal string
            total_rewards_str = analytics_stats.get('totalRealizedRewards', '0')
            try:
                # Convert decimal string to int (it's already in wei)
                total_rewards = int(float(total_rewards_str)) if total_rewards_str else 0
            except (ValueError, TypeError):
                total_rewards = 0
            
            # Get total unrealized rewards
            total_unrealized_str = analytics_stats.get('totalUnrealizedRewards', '0')
            try:
                # Convert decimal string to int (it's already in wei)
                total_unrealized_rewards = int(float(total_unrealized_str)) if total_unrealized_str else 0
            except (ValueError, TypeError):
                total_unrealized_rewards = 0
            
            # Get total unstaked tokens (withdrawn)
            total_unstaked_str = analytics_stats.get('totalUnstakedTokens', '0')
            try:
                total_unstaked = int(float(total_unstaked_str)) if total_unstaked_str else 0
            except (ValueError, TypeError):
                total_unstaked = 0
            
            # Get per-delegation rewards for display
            analytics_stakes = analytics_stats.get('stakes', [])
            # Create maps of indexer_id -> realizedRewards and unrealizedRewards (in wei)
            # Process ALL stakes (active and closed) to get complete picture
            for stake in analytics_stakes:
                indexer_id = stake.get('indexer', {}).get('id')
                if not indexer_id:
                    continue
                
                staked_tokens_str = stake.get('stakedTokens', '0')
                locked_tokens_str = stake.get('lockedTokens', '0')
                try:
                    staked_tokens = int(float(staked_tokens_str)) if staked_tokens_str else 0
                    locked_tokens = int(float(locked_tokens_str)) if locked_tokens_str else 0
                except (ValueError, TypeError):
                    staked_tokens = 0
                    locked_tokens = 0
                
                # Get realized rewards (from closed/undelegated stakes)
                # These represent rewards that were realized when allocations were closed
                realized_rewards_str = stake.get('realizedRewards', '0')
                try:
                    # Convert decimal string to int (it's already in wei)
                    realized_rewards = int(float(realized_rewards_str)) if realized_rewards_str else 0
                    # Sum up realized rewards for the same indexer (from all stakes, including closed)
                    if realized_rewards > 0:
                        if indexer_id.lower() in indexer_rewards_map:
                            indexer_rewards_map[indexer_id.lower()] += realized_rewards
                        else:
                            indexer_rewards_map[indexer_id.lower()] = realized_rewards
                except (ValueError, TypeError):
                    pass
                
                # Get unrealized rewards (only from active stakes: stakedTokens > 0 and lockedTokens == 0)
                if staked_tokens > 0 and locked_tokens == 0:
                    unrealized_rewards_str = stake.get('unrealizedRewards', '0')
                    try:
                        # Convert decimal string to int (it's already in wei)
                        unrealized_rewards = int(float(unrealized_rewards_str)) if unrealized_rewards_str else 0
                        # Sum up unrealized rewards for the same indexer
                        if indexer_id.lower() in indexer_unrealized_map:
                            indexer_unrealized_map[indexer_id.lower()] += unrealized_rewards
                        else:
                            indexer_unrealized_map[indexer_id.lower()] = unrealized_rewards
                    except (ValueError, TypeError):
                        pass
            
            # Recalculate total_staked from analytics subgraph if available (more accurate)
            # Otherwise use network subgraph
            if analytics_stats:
                # Use analytics totalStakedTokens which is the sum of all active stakes
                total_staked_str = analytics_stats.get('totalStakedTokens', '0')
                try:
                    total_staked = int(float(total_staked_str)) if total_staked_str else 0
                except (ValueError, TypeError):
                    # Fallback to network subgraph calculation
                    total_staked = sum(
                        int(d.get('stakedTokens', '0'))
                        for d in delegations
                        if is_active_delegation(d, indexer_delegations)
                    )
            else:
                # Fallback to network subgraph calculation
                total_staked = sum(
                    int(d.get('stakedTokens', '0'))
                    for d in delegations
                    if is_active_delegation(d, indexer_delegations)
                )
    
    # Calculate correct totals from aggregated stakes
    # total_staked from analytics.totalStakedTokens includes historical amounts, need to recalculate
    if analytics_client and analytics_stats:
        # Aggregate stakes by indexer to get correct totals
        indexer_stake_agg = {}
        for stake in analytics_stats.get('stakes', []):
            idx_id = stake.get('indexer', {}).get('id')
            if not idx_id:
                continue
            staked = int(float(stake.get('stakedTokens', '0')))
            locked = int(float(stake.get('lockedTokens', '0')))
            if idx_id not in indexer_stake_agg:
                indexer_stake_agg[idx_id] = {'staked': 0, 'locked': 0}
            indexer_stake_agg[idx_id]['staked'] += staked
            indexer_stake_agg[idx_id]['locked'] += locked
        
        # Calculate real totals
        total_staked = sum(t['staked'] for t in indexer_stake_agg.values() if t['staked'] > 0 and t['locked'] == 0)
        total_locked = sum(t['locked'] for t in indexer_stake_agg.values() if t['locked'] > 0)
    
    # Display summary will be printed after we calculate accumulated rewards
    # (see below after indexer_accrued_map is populated)
    
    # Build list of active delegations from analytics subgraph if available (most accurate)
    # Analytics subgraph has the correct view of active stakes
    active_delegations_list = []
    active_indexers_set = set()
    
    if analytics_client and analytics_stats:
        # Use analytics subgraph as primary source for active delegations
        # IMPORTANT: Aggregate stakes per indexer to handle split entries
        # (e.g., one active entry and one thawing entry for same indexer)
        analytics_stakes = analytics_stats.get('stakes', [])
        indexer_stake_totals = {}  # indexer_id -> {'staked': sum, 'locked': sum}
        
        for stake in analytics_stakes:
            indexer_id = stake.get('indexer', {}).get('id')
            if not indexer_id:
                continue
            
            staked_tokens_str = stake.get('stakedTokens', '0')
            locked_tokens_str = stake.get('lockedTokens', '0')
            try:
                staked_tokens = int(float(staked_tokens_str)) if staked_tokens_str else 0
                locked_tokens = int(float(locked_tokens_str)) if locked_tokens_str else 0
                
                if indexer_id not in indexer_stake_totals:
                    indexer_stake_totals[indexer_id] = {'staked': 0, 'locked': 0}
                indexer_stake_totals[indexer_id]['staked'] += staked_tokens
                indexer_stake_totals[indexer_id]['locked'] += locked_tokens
            except (ValueError, TypeError):
                continue
        
        # Only include indexers with net positive staked tokens and no locked tokens
        for indexer_id, totals in indexer_stake_totals.items():
            net_staked = totals['staked']
            net_locked = totals['locked']
            
            # Active if net staked > 0 and not thawing
            if net_staked > 0 and net_locked == 0:
                active_delegations_list.append({
                    'indexer': {'id': indexer_id},
                    'stakedTokens': str(net_staked),
                    'lockedTokens': '0',
                    'lastUndelegatedAt': None
                })
                active_indexers_set.add(indexer_id.lower())
    else:
        # Fallback to network subgraph if analytics not available
        active_delegations_list = [
            d for d in delegations 
            if is_active_delegation(d, indexer_delegations)
        ]
        active_indexers_set = {d['indexer']['id'].lower() for d in active_delegations_list}
    
    if not active_delegations_list:
        # No active delegations - show simplified portfolio
        print_section("Portfolio")
        print(f"  {Colors.DIM}{'Staked':>20}  {'Thawing':>20}{Colors.RESET}")
        thawing_str = format_tokens(str(total_locked)) if total_locked > 0 else "-"
        print(f"  {Colors.DIM}{'-':>20}{Colors.RESET}  ", end='')
        if total_locked > 0:
            print(f"{Colors.CYAN}{thawing_str:>20}{Colors.RESET}")
            print()
            print(f"  {Colors.BOLD}Total Value:{Colors.RESET} {Colors.WHITE}{format_tokens(str(total_locked))}{Colors.RESET}")
        else:
            print(f"{Colors.DIM}{'-':>20}{Colors.RESET}")
        # Withdrawn = totalUnstaked - currently thawing (lockedTokens)
        actual_withdrawn = total_unstaked - total_locked if total_unstaked > total_locked else 0
        if actual_withdrawn > 0:
            print(f"  {Colors.DIM}Withdrawn: {format_tokens(str(actual_withdrawn))} (lifetime){Colors.RESET}")
        
        print_section("Active Delegations")
        print(f"  {Colors.DIM}No active delegations.{Colors.RESET}")
    else:
        # Get indexer details for all indexers
        indexer_details = {}
        for d in active_delegations_list:
            indexer_id = d['indexer']['id']
            if indexer_id not in indexer_details:
                indexer_details[indexer_id] = client.get_indexer_details(indexer_id)
        
        # Sort by staked amount
        active_delegations_list.sort(key=lambda x: int(x.get('stakedTokens', '0')), reverse=True)
        
        # Resolve ENS names for all indexers in batch (faster)
        indexer_ids_to_resolve = [d['indexer']['id'] for d in active_delegations_list]
        if ens_client and indexer_ids_to_resolve:
            ens_names_batch = ens_client.resolve_addresses_batch(indexer_ids_to_resolve)
        else:
            ens_names_batch = {}
        
        # Get active allocations for calculating unrealized rewards (fallback if analytics not available)
        # Use the active indexers from analytics if available
        active_indexer_ids = [d['indexer']['id'] for d in active_delegations_list] if active_delegations_list else []
        active_allocations = client.get_delegator_allocations(delegator_id, active_only=True, indexer_ids=active_indexer_ids) if active_indexer_ids else []
        
        # Get closed allocations for calculating accrued rewards from past allocations (pre-Horizon and Horizon)
        # Get ALL closed allocations (not just recent ones) to calculate total rewards correctly
        # We'll use the approach: total rewards (all allocations) - unrealized (active) = accrued (closed)
        closed_allocations = []
        if active_indexer_ids:
            # Query ALL closed allocations (no time cutoff) to get complete picture
            # We need all closed allocations to calculate total rewards correctly
            # Use pagination to get all closed allocations (not just first 1000)
            closed_allocations = []
            skip = 0
            batch_size = 1000
            
            while True:
                query = """
                query GetClosedAllocations($indexers: [String!]!, $skip: Int!) {
                    allocations(
                        where: { 
                            indexer_in: $indexers, 
                            status: Closed
                        }
                        orderBy: closedAt
                        orderDirection: desc
                        first: $batch_size
                        skip: $skip
                    ) {
                        id
                        indexer {
                            id
                        }
                        allocatedTokens
                        createdAt
                        closedAt
                        subgraphDeployment {
                            ipfsHash
                            signalledTokens
                        }
                    }
                }
                """
                result = client.query(query, {'indexers': active_indexer_ids, 'skip': skip, 'batch_size': batch_size})
                batch = result.get('allocations', [])
                if not batch:
                    break
                closed_allocations.extend(batch)
                if len(batch) < batch_size:
                    break
                skip += batch_size
        
        # Map of indexer_id -> unrealized rewards (from active allocations/Horizon)
        indexer_unrealized_map_display = {}
        # Map of indexer_id -> total rewards (from all allocations - active + closed)
        indexer_total_rewards_map = {}
        # Map of indexer_id -> accrued rewards (from closed allocations) = total - unrealized
        indexer_accrued_map = {}
        
        # Calculate unrealized rewards from active allocations (fallback if analytics not available)
        if active_allocations:
            # Group allocations by indexer
            indexer_active_allocations = {}
            for alloc in active_allocations:
                indexer_id = alloc['indexer']['id']
                if indexer_id not in indexer_active_allocations:
                    indexer_active_allocations[indexer_id] = []
                indexer_active_allocations[indexer_id].append(alloc)
            
            # Collect all allocation IDs for batch processing
            all_allocation_ids = []
            for allocs in indexer_active_allocations.values():
                for alloc in allocs:
                    all_allocation_ids.append(alloc['id'])
            
            # Fetch all rewards in parallel
            if all_allocation_ids:
                rewards_map = get_accrued_rewards_batch(all_allocation_ids)
                
                # Calculate unrealized rewards from active allocations for each indexer (fallback)
                for indexer_id, allocs in indexer_active_allocations.items():
                    indexer_unrealized = 0.0
                    for alloc in allocs:
                        allocation_id = alloc['id']
                        accrued = rewards_map.get(allocation_id)
                        if accrued:
                            indexer_unrealized += accrued
                    if indexer_unrealized > 0:
                        indexer_unrealized_map_display[indexer_id.lower()] = int(indexer_unrealized * 1e18)
        
        # Calculate total rewards per indexer by summing rewards from ALL allocations (active + closed)
        # Then accrued rewards from closed allocations = total - unrealized (from active)
        if active_indexer_ids:
            # Get ALL allocations (active + closed) for these indexers
            all_allocation_ids = []
            
            # Add active allocations
            for alloc in active_allocations:
                all_allocation_ids.append(alloc['id'])
            
            # Add closed allocations
            if closed_allocations:
                for alloc in closed_allocations:
                    all_allocation_ids.append(alloc['id'])
            
            # Fetch all allocation rewards in parallel (both active and closed)
            # Use more workers for large batches
            if all_allocation_ids:
                max_workers = min(20, len(all_allocation_ids))
                all_rewards_map = get_accrued_rewards_batch(all_allocation_ids, max_workers=max_workers)
                
                # Group allocations by indexer
                indexer_all_allocations = {}
                for alloc in active_allocations + closed_allocations:
                    indexer_id = alloc['indexer']['id']
                    if indexer_id not in indexer_all_allocations:
                        indexer_all_allocations[indexer_id] = []
                    indexer_all_allocations[indexer_id].append(alloc)
                
                # Calculate total rewards per indexer (from all allocations: active + closed)
                for indexer_id, allocs in indexer_all_allocations.items():
                    indexer_total = 0.0
                    for alloc in allocs:
                        allocation_id = alloc['id']
                        rewards = all_rewards_map.get(allocation_id)
                        if rewards and rewards > 0:
                            indexer_total += rewards
                    
                    if indexer_total > 0:
                        indexer_total_rewards_map[indexer_id.lower()] = int(indexer_total * 1e18)
        
        # Calculate accrued rewards from pool share value
        # Accrued = (delegatedTokens * shareAmount / delegatorShares) - CURRENT_stakedTokens
        # IMPORTANT: Use stakedTokens from analytics (current) not network (historical)
        # Network subgraph includes undelegated amounts in stakedTokens
        
        # Build map of current stake from analytics
        analytics_stake_map = {}
        if analytics_client and analytics_stats:
            for stake in analytics_stats.get('stakes', []):
                idx_id = stake.get('indexer', {}).get('id', '').lower()
                if idx_id:
                    analytics_stake_map[idx_id] = int(float(stake.get('stakedTokens', '0')))
        
        for d in delegations:
            indexer_id = d.get('indexer', {}).get('id')
            if not indexer_id:
                continue
            indexer_id_lower = indexer_id.lower()
            
            # Get pool info from network subgraph delegation
            indexer_info = d.get('indexer', {})
            pool_tokens = int(indexer_info.get('delegatedTokens', '0'))
            pool_shares = int(indexer_info.get('delegatorShares', '0'))
            my_shares = int(d.get('shareAmount', '0'))
            
            # Use CURRENT stake from analytics, not historical from network
            current_stake = analytics_stake_map.get(indexer_id_lower, 0)
            if current_stake == 0:
                # Fallback to network if analytics not available
                current_stake = int(d.get('stakedTokens', '0'))
            
            if pool_shares > 0 and my_shares > 0 and current_stake > 0:
                # Calculate my actual balance in the pool
                my_balance = (pool_tokens * my_shares) // pool_shares
                accrued = my_balance - current_stake
                # Store all values (positive and negative)
                indexer_accrued_map[indexer_id_lower] = accrued
        
        # Calculate total accumulated (sum of positive accrued values)
        total_accumulated = sum(v for v in indexer_accrued_map.values() if v > 0)
        total_pending = sum(indexer_unrealized_map.values()) if indexer_unrealized_map else 0
        
        # Display Portfolio Summary
        print_section("Portfolio")
        total_value = total_staked + total_accumulated + total_locked
        print(f"  {Colors.DIM}{'Staked':>20}  {'Accumulated':>20}  {'Thawing':>20}{Colors.RESET}")
        staked_str = format_tokens(str(total_staked))
        accumulated_str = format_tokens(str(total_accumulated))
        thawing_str = format_tokens(str(total_locked)) if total_locked > 0 else "-"
        print(f"  {Colors.BRIGHT_GREEN}{staked_str:>20}{Colors.RESET}  ", end='')
        print(f"{Colors.YELLOW}{accumulated_str:>20}{Colors.RESET}  ", end='')
        if total_locked > 0:
            print(f"{Colors.CYAN}{thawing_str:>20}{Colors.RESET}")
        else:
            print(f"{Colors.DIM}{thawing_str:>20}{Colors.RESET}")
        print()
        print(f"  {Colors.BOLD}Total Value:{Colors.RESET} {Colors.WHITE}{format_tokens(str(total_value))}{Colors.RESET}")
        if total_pending > 0:
            print(f"  {Colors.DIM}+ Pending: {format_tokens(str(total_pending))} (from active allocations){Colors.RESET}")
        # Withdrawn = totalUnstaked - currently thawing (lockedTokens)
        actual_withdrawn = total_unstaked - total_locked if total_unstaked > total_locked else 0
        if actual_withdrawn > 0:
            print(f"  {Colors.DIM}Withdrawn: {format_tokens(str(actual_withdrawn))} (lifetime){Colors.RESET}")
        
        # Active Delegations section
        print_section(f"Active Delegations ({len(active_delegations_list)})")
        print(f"  {Colors.DIM}{'Indexer':<28} {'Staked':>20}    {'Value':>20}  {'Profit':>20}{Colors.RESET}")
        
        for d in active_delegations_list:
            indexer_id = d['indexer']['id']
            indexer_info = indexer_details.get(indexer_id, {})
            staked = int(d.get('stakedTokens', '0'))
            
            # Get unrealized rewards from analytics subgraph first (from active allocations/Horizon)
            unrealized = indexer_unrealized_map.get(indexer_id.lower(), 0) if analytics_client else 0
            
            # Fallback to calculating from active allocations if analytics not available
            if unrealized == 0:
                unrealized = indexer_unrealized_map_display.get(indexer_id.lower(), 0)
            
            # Get accrued rewards from closed allocations (pre-Horizon)
            accrued = indexer_accrued_map.get(indexer_id.lower(), 0)
            
            # Get indexer ENS from batch resolution (use lowercase for lookup)
            indexer_ens = ens_names_batch.get(indexer_id.lower()) if ens_names_batch else None
            indexer_display = indexer_ens or f"{indexer_id[:10]}.."
            
            # Get reward cut
            reward_cut = None
            if indexer_info:
                reward_cut_val = indexer_info.get('rewardCut')
                if reward_cut_val is not None:
                    reward_cut = int(reward_cut_val) / 1e6
            
            # Calculate value and profit
            value = staked + accrued  # Current value in pool
            profit_pct = (accrued / staked * 100) if staked > 0 else 0
            
            # Format values
            staked_str = format_tokens(str(staked))
            value_str = format_tokens(str(value)) if value > 0 else "-"
            
            # Format profit with percentage
            if accrued > 0:
                profit_str = f"+{format_tokens(str(accrued))} ({profit_pct:+.0f}%)"
            elif accrued < 0 and abs(profit_pct) >= 1.0:
                profit_str = f"{format_tokens(str(accrued))} ({profit_pct:+.0f}%)"
            elif accrued < 0:
                profit_str = "~0"
            else:
                profit_str = "-"
            
            # Print row: Indexer  Staked  →  Value  Profit
            print(f"  {Colors.WHITE}{indexer_display:<28}{Colors.RESET}", end='')
            print(f" {Colors.DIM}{staked_str:>20}{Colors.RESET}", end='')
            print(f" {Colors.DIM}→{Colors.RESET}", end='')
            print(f" {Colors.BRIGHT_GREEN}{value_str:>20}{Colors.RESET}", end='')
            
            # Profit column with color
            if accrued > 0:
                print(f"  {Colors.YELLOW}{profit_str:>20}{Colors.RESET}")
            elif accrued < 0 and abs(profit_pct) >= 1.0:
                print(f"  {Colors.RED}{profit_str:>20}{Colors.RESET}")
            else:
                print(f"  {Colors.DIM}{profit_str:>20}{Colors.RESET}")
    
    # Save cache after all operations
    _save_accrued_rewards_cache()
    
    # Update active_delegations for use in unrealized rewards section
    active_delegations = active_delegations_list
    
    # Thawing delegations
    thawing_delegations = [d for d in delegations if int(d.get('lockedTokens', '0')) > 0]
    if thawing_delegations:
        print_section("Thawing Delegations")
        print(f"  {Colors.DIM}{'Indexer':<35} {'Amount':>18} {'Status':>20}{Colors.RESET}")
        
        # Resolve ENS names for thawing delegations in batch
        thawing_indexer_ids = [d['indexer']['id'] for d in thawing_delegations]
        if ens_client and thawing_indexer_ids:
            thawing_ens_names = ens_client.resolve_addresses_batch(thawing_indexer_ids)
        else:
            thawing_ens_names = {}
        
        for d in thawing_delegations:
            indexer_id = d['indexer']['id']
            locked = int(d.get('lockedTokens', '0'))
            last_undelegated = d.get('lastUndelegatedAt')
            
            indexer_ens = thawing_ens_names.get(indexer_id.lower()) if thawing_ens_names else None
            indexer_display = indexer_ens or f"{indexer_id[:10]}.."
            
            time_info = ""
            if last_undelegated:
                try:
                    undelegated_time = datetime.fromtimestamp(int(last_undelegated))
                    now = datetime.now()
                    elapsed = now - undelegated_time
                    # Thawing period is typically 28 days (28 * 24 * 3600 seconds)
                    thaw_period_seconds = 28 * 24 * 3600
                    remaining_seconds = thaw_period_seconds - int(elapsed.total_seconds())
                    
                    if remaining_seconds > 0:
                        days = remaining_seconds // 86400
                        hours = (remaining_seconds % 86400) // 3600
                        minutes = (remaining_seconds % 3600) // 60
                        if days > 0:
                            time_info = f"{days}d {hours}h remaining"
                        elif hours > 0:
                            time_info = f"{hours}h {minutes}m remaining"
                        else:
                            time_info = f"{minutes}m remaining"
                    else:
                        time_info = "Ready to withdraw"
                except Exception as e:
                    time_info = f"Unknown ({str(e)[:20]})"
            else:
                time_info = "Unknown"
            
            print(f"  {Colors.WHITE}{indexer_display:35}{Colors.RESET}  {Colors.YELLOW}{format_tokens(str(locked)):>18}{Colors.RESET}  {Colors.DIM}{time_info:>20}{Colors.RESET}")
    
    # Unrealized rewards are now shown in the Active Delegations section above
    # Only show a summary section if there are unrealized rewards from analytics
    if active_delegations_list and analytics_client and total_unrealized_rewards > 0:
        print_section("Unrealized Rewards Summary")
        print(f"{Colors.BOLD}Total Unrealized:{Colors.RESET} {Colors.BRIGHT_CYAN}{format_tokens(str(total_unrealized_rewards))}{Colors.RESET}\n")
    
    # Legacy fallback: Get allocations to show accrued rewards (only if no analytics data)
    # Only show if there are active delegations AND no analytics client
    if active_delegations_list and not analytics_client:
        allocations = client.get_delegator_allocations(delegator_id, active_only=True)
        if allocations:
            print_section("Accrued Rewards (from Active Allocations)")
        
        # Group allocations by indexer
        indexer_allocations = {}
        for alloc in allocations:
            indexer_id = alloc['indexer']['id']
            if indexer_id not in indexer_allocations:
                indexer_allocations[indexer_id] = []
            indexer_allocations[indexer_id].append(alloc)
        
        total_accrued = 0.0
        total_delegator_share = 0.0
        
        for indexer_id, allocs in indexer_allocations.items():
            # Use batch-resolved ENS names if available, otherwise resolve individually
            indexer_ens = None
            if ens_client:
                # Try batch first (if we resolved earlier)
                if 'ens_names_batch' in locals() and indexer_id.lower() in ens_names_batch:
                    indexer_ens = ens_names_batch[indexer_id.lower()]
                else:
                    indexer_ens = ens_client.resolve_address(indexer_id)
            indexer_display = indexer_ens or f"{indexer_id[:10]}.."
            
            # Batch fetch rewards for this indexer's allocations
            allocation_ids = [alloc['id'] for alloc in allocs]
            rewards_map = get_accrued_rewards_batch(allocation_ids)
            
            indexer_accrued = 0.0
            for alloc in allocs:
                allocation_id = alloc['id']
                accrued = rewards_map.get(allocation_id)
                if accrued:
                    indexer_accrued += accrued
            
            if indexer_accrued > 0:
                total_accrued += indexer_accrued
                reward_cut = get_indexer_reward_cut(indexer_id, network_url)
                if reward_cut is not None:
                    delegator_share = indexer_accrued * (1 - reward_cut)
                    total_delegator_share += delegator_share
                    print(f"  {Colors.WHITE}{indexer_display:35}{Colors.RESET}  {Colors.BRIGHT_CYAN}{format_tokens(str(int(indexer_accrued * 1e18))):>18}{Colors.RESET}", end='')
                    print(f"  {Colors.DIM}(your share: {format_tokens(str(int(delegator_share * 1e18)))}){Colors.RESET}")
                else:
                    print(f"  {Colors.WHITE}{indexer_display:35}{Colors.RESET}  {Colors.BRIGHT_CYAN}{format_tokens(str(int(indexer_accrued * 1e18))):>18}{Colors.RESET}")
        
        if total_accrued == 0:
            print(f"{Colors.DIM}No accrued rewards found (install web3 for exact values){Colors.RESET}")
        elif total_accrued > 0:
            print(f"\n{Colors.BOLD}Total Accrued:{Colors.RESET} {Colors.BRIGHT_CYAN}{format_tokens(str(int(total_accrued * 1e18)))}{Colors.RESET}")
            if total_delegator_share > 0:
                print(f"{Colors.BOLD}Your Share:{Colors.RESET} {Colors.BRIGHT_GREEN}{format_tokens(str(int(total_delegator_share * 1e18)))}{Colors.RESET}")


if __name__ == "__main__":
    main()


