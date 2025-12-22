#!/usr/bin/env python3
"""
Unit tests for config.py module
"""

import pytest
import sys
import os
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import (
    get_network_subgraph_url,
    get_my_indexer_id,
    get_ens_subgraph_url,
    get_rpc_url
)


class TestGetNetworkSubgraphUrl:
    """Tests for network subgraph URL configuration"""
    
    def test_returns_string_or_none(self):
        result = get_network_subgraph_url()
        assert result is None or isinstance(result, str)
    
    @patch.dict(os.environ, {'THEGRAPH_NETWORK_SUBGRAPH_URL': 'https://test.example.com'})
    def test_env_var_override(self):
        result = get_network_subgraph_url()
        assert result == 'https://test.example.com'
    
    def test_config_file_reading(self):
        # This test verifies the function handles config file reading
        # The exact result depends on the actual config file present
        # We just verify it returns the right type
        result = get_network_subgraph_url()
        assert result is None or isinstance(result, str)


class TestGetMyIndexerId:
    """Tests for indexer ID configuration"""
    
    def test_returns_string_or_none(self):
        result = get_my_indexer_id()
        assert result is None or isinstance(result, str)
    
    @patch.dict(os.environ, {'MY_INDEXER_ID': '0x1234567890abcdef'})
    def test_env_var_override(self):
        result = get_my_indexer_id()
        assert result == '0x1234567890abcdef'


class TestGetEnsSubgraphUrl:
    """Tests for ENS subgraph URL configuration"""
    
    def test_returns_string_or_none(self):
        result = get_ens_subgraph_url()
        assert result is None or isinstance(result, str)
    
    @patch.dict(os.environ, {'ENS_SUBGRAPH_URL': 'https://ens.example.com'})
    def test_env_var_override(self):
        result = get_ens_subgraph_url()
        assert result == 'https://ens.example.com'


class TestGetRpcUrl:
    """Tests for RPC URL configuration"""
    
    def test_returns_string_or_none(self):
        result = get_rpc_url()
        assert result is None or isinstance(result, str)
    
    @patch.dict(os.environ, {'RPC_URL': 'https://rpc.example.com'})
    def test_env_var_override(self):
        result = get_rpc_url()
        assert result == 'https://rpc.example.com'


if __name__ == '__main__':
    pytest.main([__file__, '-v'])

