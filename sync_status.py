#!/usr/bin/env python3
"""
Shared module for querying indexer sync status from Graph Node /status endpoints
"""

from typing import Dict, Optional
import requests


class Colors:
    """ANSI color codes - duplicated here for standalone use"""
    RESET = '\033[0m'
    DIM = '\033[2m'
    BRIGHT_GREEN = '\033[92m'
    BRIGHT_RED = '\033[91m'
    BRIGHT_YELLOW = '\033[93m'


class IndexerStatusClient:
    """Client to query indexer status endpoints for sync information"""
    
    def __init__(self, timeout: int = 15):
        self._session = requests.Session()
        self._timeout = timeout
        self.last_error = None
        self.last_url = None
    
    def get_deployment_status(self, indexer_url: str, deployment_hash: str) -> Optional[Dict]:
        """Get sync status for a specific deployment
        
        Returns: status info dict or None if not found/error:
        {
            'synced': bool,
            'health': 'healthy' | 'unhealthy' | 'failed',
            'latestBlock': int,
            'chainHeadBlock': int,
            'blocksBehind': int,
            'network': str,
            'fatalError': Optional[str]
        }
        """
        statuses = self.get_all_deployments_status(indexer_url)
        return statuses.get(deployment_hash)
    
    def get_all_deployments_status(self, indexer_url: str) -> Dict[str, Dict]:
        """Get sync status for all deployments from an indexer's status endpoint
        
        Returns: dict mapping deployment IPFS hash to status info:
        {
            'QmXyz...': {
                'synced': bool,
                'health': 'healthy' | 'unhealthy' | 'failed',
                'latestBlock': int,
                'chainHeadBlock': int,
                'blocksBehind': int,
                'network': str,
                'fatalError': Optional[str]
            }
        }
        
        On error, sets self.last_error with error details
        """
        self.last_error = None
        self.last_url = None
        
        if not indexer_url:
            self.last_error = "No indexer URL configured"
            return {}
        
        # Normalize URL
        if not indexer_url.startswith('http'):
            indexer_url = f"https://{indexer_url}"
        status_url = f"{indexer_url.rstrip('/')}/status"
        self.last_url = status_url
        
        try:
            response = self._session.post(
                status_url,
                json={
                    'query': '''
                    {
                        indexingStatuses {
                            subgraph
                            synced
                            health
                            fatalError { message }
                            chains { 
                                network
                                latestBlock { number } 
                                chainHeadBlock { number } 
                            }
                        }
                    }
                    '''
                },
                timeout=self._timeout
            )
            response.raise_for_status()
            data = response.json()
            
            # Check for GraphQL errors
            if 'errors' in data:
                self.last_error = f"GraphQL error: {data['errors'][0].get('message', 'Unknown')}"
                return {}
            
            statuses = data.get('data', {}).get('indexingStatuses', [])
            result = {}
            
            for s in statuses:
                deployment = s.get('subgraph', '')
                if not deployment:
                    continue
                
                chains = s.get('chains') or []
                chain = chains[0] if chains else {}
                
                latest_block = chain.get('latestBlock') or {}
                head_block = chain.get('chainHeadBlock') or {}
                latest = int(latest_block.get('number', 0) or 0)
                head = int(head_block.get('number', 0) or 0)
                
                fatal_error = None
                if s.get('fatalError'):
                    fatal_error = s['fatalError'].get('message', 'Unknown error')
                
                result[deployment] = {
                    'synced': s.get('synced', False),
                    'health': s.get('health', 'unknown'),
                    'latestBlock': latest,
                    'chainHeadBlock': head,
                    'blocksBehind': max(0, head - latest),
                    'network': chain.get('network', ''),
                    'fatalError': fatal_error
                }
            
            return result
            
        except requests.exceptions.Timeout:
            self.last_error = f"Timeout ({self._timeout}s) - endpoint may be slow or unreachable"
            return {}
        except requests.exceptions.ConnectionError:
            self.last_error = "Connection failed - endpoint may be blocked or not exposed"
            return {}
        except requests.exceptions.HTTPError as e:
            status_code = e.response.status_code if e.response else 'unknown'
            if status_code == 404:
                self.last_error = "Endpoint not found (404) - /status may not be exposed"
            elif status_code == 403:
                self.last_error = "Access denied (403) - endpoint may require authentication"
            else:
                self.last_error = f"HTTP error {status_code}"
            return {}
        except Exception as e:
            self.last_error = f"{type(e).__name__}: {str(e)[:50]}"
            return {}


def format_sync_status(status: Optional[Dict], colors: type = Colors) -> str:
    """Format sync status as a colored indicator
    
    Args:
        status: Status dict from IndexerStatusClient
        colors: Colors class to use for formatting (allows using caller's Colors)
    
    Note: We don't trust the 'synced' boolean alone because Graph Node can report
    synced=True even when blocksBehind is significant. We prioritize blocksBehind.
    """
    if not status:
        return f"{colors.DIM}?{colors.RESET}"
    
    health = status.get('health', 'unknown')
    behind = status.get('blocksBehind', 0)
    synced = status.get('synced', False)
    
    # Failed takes priority
    if health == 'failed':
        return f"{colors.BRIGHT_RED}✗ failed{colors.RESET}"
    
    # Check blocksBehind first - don't trust synced if significantly behind
    if behind > 100:
        if behind > 10000:
            behind_str = f"{behind/1000:.0f}k"
        else:
            behind_str = f"{behind:,}"
        return f"{colors.BRIGHT_YELLOW}↻ -{behind_str}{colors.RESET}"
    
    # Only report synced if blocksBehind is small
    if synced or behind == 0:
        return f"{colors.BRIGHT_GREEN}✓ synced{colors.RESET}"
    
    # Small number of blocks behind
    if behind > 0:
        return f"{colors.BRIGHT_YELLOW}↻ -{behind}{colors.RESET}"
    
    return f"{colors.DIM}syncing{colors.RESET}"


def format_sync_status_detailed(status: Optional[Dict], colors: type = Colors) -> str:
    """Format sync status with more detail (blocks behind, network)
    
    Args:
        status: Status dict from IndexerStatusClient
        colors: Colors class to use for formatting
    
    Note: We don't trust the 'synced' boolean alone because Graph Node can report
    synced=True even when blocksBehind is significant. We prioritize blocksBehind.
    """
    if not status:
        return f"{colors.DIM}No status available{colors.RESET}"
    
    health = status.get('health', 'unknown')
    behind = status.get('blocksBehind', 0)
    synced = status.get('synced', False)
    network = status.get('network', '')
    latest = status.get('latestBlock', 0)
    head = status.get('chainHeadBlock', 0)
    fatal_error = status.get('fatalError')
    
    network_str = f" on {network}" if network else ""
    
    # Failed takes priority
    if health == 'failed':
        error_msg = f": {fatal_error[:50]}..." if fatal_error and len(fatal_error) > 50 else (f": {fatal_error}" if fatal_error else "")
        return f"{colors.BRIGHT_RED}✗ Failed{network_str}{error_msg}{colors.RESET}"
    
    # Check blocksBehind first - don't trust synced if significantly behind
    if behind > 100:
        if behind > 10000:
            behind_str = f"{behind/1000:.0f}k"
        else:
            behind_str = f"{behind:,}"
        return f"{colors.BRIGHT_YELLOW}↻ Syncing{network_str} (-{behind_str} blocks, at {latest:,}/{head:,}){colors.RESET}"
    
    # Only report synced if blocksBehind is small
    if synced or behind == 0:
        return f"{colors.BRIGHT_GREEN}✓ Synced{network_str} (block {latest:,}){colors.RESET}"
    
    # Small number of blocks behind
    if behind > 0:
        return f"{colors.BRIGHT_YELLOW}↻ Syncing{network_str} (-{behind} blocks){colors.RESET}"
    
    return f"{colors.DIM}Unknown status{network_str}{colors.RESET}"

