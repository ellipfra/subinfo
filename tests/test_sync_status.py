#!/usr/bin/env python3
"""
Unit tests for sync_status.py module
"""

import pytest
import sys
import os
from unittest.mock import Mock, patch

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sync_status import IndexerStatusClient, format_sync_status, format_sync_status_detailed, Colors


class TestIndexerStatusClient:
    """Tests for IndexerStatusClient class"""
    
    def test_init_default_timeout(self):
        client = IndexerStatusClient()
        assert client._timeout == 15
    
    def test_init_custom_timeout(self):
        client = IndexerStatusClient(timeout=30)
        assert client._timeout == 30
    
    def test_get_all_deployments_status_no_url(self):
        client = IndexerStatusClient()
        result = client.get_all_deployments_status("")
        assert result == {}
        assert client.last_error == "No indexer URL configured"
    
    def test_get_all_deployments_status_normalizes_url(self):
        client = IndexerStatusClient()
        # Test that URL normalization works
        with patch.object(client._session, 'post') as mock_post:
            mock_response = Mock()
            mock_response.json.return_value = {'data': {'indexingStatuses': []}}
            mock_response.raise_for_status = Mock()
            mock_post.return_value = mock_response
            
            client.get_all_deployments_status("example.com")
            
            # Should have added https:// and /status
            call_args = mock_post.call_args
            assert call_args[0][0] == "https://example.com/status"
    
    def test_get_deployment_status_not_found(self):
        client = IndexerStatusClient()
        with patch.object(client, 'get_all_deployments_status') as mock_method:
            mock_method.return_value = {}
            result = client.get_deployment_status("https://example.com", "QmNotFound")
            assert result is None
    
    def test_get_deployment_status_found(self):
        client = IndexerStatusClient()
        mock_statuses = {
            'QmFound123': {
                'synced': True,
                'health': 'healthy',
                'latestBlock': 1000,
                'chainHeadBlock': 1000,
                'blocksBehind': 0,
                'network': 'mainnet',
                'fatalError': None
            }
        }
        with patch.object(client, 'get_all_deployments_status') as mock_method:
            mock_method.return_value = mock_statuses
            result = client.get_deployment_status("https://example.com", "QmFound123")
            assert result is not None
            assert result['synced'] == True
            assert result['health'] == 'healthy'


class TestFormatSyncStatus:
    """Tests for format_sync_status function"""
    
    def test_format_sync_status_none(self):
        result = format_sync_status(None, Colors)
        assert '?' in result
    
    def test_format_sync_status_synced(self):
        status = {
            'synced': True,
            'health': 'healthy',
            'blocksBehind': 0
        }
        result = format_sync_status(status, Colors)
        assert 'synced' in result.lower() or '✓' in result
    
    def test_format_sync_status_failed(self):
        status = {
            'synced': False,
            'health': 'failed',
            'blocksBehind': 0
        }
        result = format_sync_status(status, Colors)
        assert 'failed' in result.lower() or '✗' in result
    
    def test_format_sync_status_syncing_small_behind(self):
        status = {
            'synced': False,
            'health': 'healthy',
            'blocksBehind': 50
        }
        result = format_sync_status(status, Colors)
        # Should show blocks behind for small numbers
        assert '50' in result or 'synced' in result.lower()
    
    def test_format_sync_status_syncing_large_behind(self):
        status = {
            'synced': False,
            'health': 'healthy',
            'blocksBehind': 15000
        }
        result = format_sync_status(status, Colors)
        # Should show blocks behind in "k" format
        assert '15k' in result.lower() or '15000' in result
    
    def test_format_sync_status_false_synced_but_behind(self):
        """Test that we don't trust synced=True when blocksBehind is significant"""
        status = {
            'synced': True,  # Graph Node says synced
            'health': 'healthy',
            'blocksBehind': 300000  # But actually 300k behind!
        }
        result = format_sync_status(status, Colors)
        # Should NOT show as synced, should show blocks behind
        assert 'synced' not in result.lower() or '300' in result


class TestFormatSyncStatusDetailed:
    """Tests for format_sync_status_detailed function"""
    
    def test_detailed_none(self):
        result = format_sync_status_detailed(None, Colors)
        assert 'No status' in result or 'unavailable' in result.lower()
    
    def test_detailed_synced(self):
        status = {
            'synced': True,
            'health': 'healthy',
            'latestBlock': 20000000,
            'chainHeadBlock': 20000000,
            'blocksBehind': 0,
            'network': 'mainnet'
        }
        result = format_sync_status_detailed(status, Colors)
        assert 'Synced' in result
        assert 'mainnet' in result.lower()
        assert '20,000,000' in result or '20000000' in result
    
    def test_detailed_syncing(self):
        status = {
            'synced': False,
            'health': 'healthy',
            'latestBlock': 19000000,
            'chainHeadBlock': 20000000,
            'blocksBehind': 1000000,
            'network': 'mainnet'
        }
        result = format_sync_status_detailed(status, Colors)
        assert 'Syncing' in result
        assert 'mainnet' in result.lower()
    
    def test_detailed_failed_with_error(self):
        status = {
            'synced': False,
            'health': 'failed',
            'blocksBehind': 0,
            'network': 'mainnet',
            'fatalError': 'Block processing failed: timeout'
        }
        result = format_sync_status_detailed(status, Colors)
        assert 'Failed' in result
        assert 'timeout' in result.lower()


if __name__ == '__main__':
    pytest.main([__file__, '-v'])

