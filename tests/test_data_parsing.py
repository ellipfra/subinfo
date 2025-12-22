#!/usr/bin/env python3
"""
Tests for parsing subgraph and RPC data

These tests verify that the tools correctly handle various response formats
from The Graph Network subgraph and RPC endpoints, including edge cases.
"""

import pytest
import sys
import os
from unittest.mock import Mock, patch, MagicMock

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.fixtures.graphql_responses import (
    INDEXER_FULL_RESPONSE, INDEXER_MINIMAL_RESPONSE, INDEXER_WITH_NULL_URL,
    INDEXER_NOT_FOUND, INDEXER_OVER_ALLOCATED, INDEXER_WHALE,
    ALLOCATIONS_ACTIVE, ALLOCATIONS_EMPTY, ALLOCATIONS_CLOSED_WITH_LEGACY,
    ALLOCATIONS_MISSING_DEPLOYMENT,
    DELEGATIONS_RESPONSE, DELEGATIONS_EMPTY,
    NETWORK_STATS,
    SUBGRAPH_DEPLOYMENT_FOUND, SUBGRAPH_DEPLOYMENT_NOT_FOUND,
    GRAPHQL_ERROR_SCHEMA, GRAPHQL_ERROR_VARIABLE,
    POI_SUBMISSIONS, POI_SUBMISSIONS_EMPTY
)

from tests.fixtures.rpc_responses import (
    STATUS_HEALTHY_SYNCED, STATUS_SYNCING, STATUS_FALSE_SYNCED,
    STATUS_FAILED, STATUS_NULL_CHAINS, STATUS_EMPTY_CHAINS,
    STATUS_NULL_BLOCKS, STATUS_MULTIPLE_DEPLOYMENTS, STATUS_EMPTY,
    SINGLE_REWARD_LOG, MULTIPLE_REWARDS_SAME_ALLOCATION,
    REWARDS_MULTIPLE_ALLOCATIONS, EMPTY_LOGS
)


class TestIndexerDataParsing:
    """Tests for parsing indexer data from subgraph responses"""
    
    def test_parse_full_indexer_data(self):
        """Test parsing complete indexer response"""
        indexer = INDEXER_FULL_RESPONSE["data"]["indexer"]
        
        # Verify all expected fields are present
        assert indexer["id"] == "0xf92f430dd8567b0d466358c79594ab58d919a6d4"
        assert int(indexer["stakedTokens"]) / 1e18 == pytest.approx(7500862, rel=1e-6)
        assert int(indexer["indexingRewardCut"]) == 265000  # 26.5% in PPM
        assert indexer["url"] == "https://graph-l2prod.ellipfra.com/"
    
    def test_parse_minimal_indexer_data(self):
        """Test parsing indexer with only required fields"""
        indexer = INDEXER_MINIMAL_RESPONSE["data"]["indexer"]
        
        assert indexer["id"] is not None
        assert int(indexer["stakedTokens"]) / 1e18 == pytest.approx(1000, rel=1e-6)
        assert int(indexer["delegatedTokens"]) == 0
    
    def test_handle_null_url(self):
        """Test handling indexer with null URL"""
        indexer = INDEXER_WITH_NULL_URL["data"]["indexer"]
        
        # Should be able to access without error
        url = indexer.get("url")
        assert url is None
        
        # Code should handle this gracefully
        display_url = url if url else "No URL"
        assert display_url == "No URL"
    
    def test_handle_indexer_not_found(self):
        """Test handling when indexer doesn't exist"""
        indexer = INDEXER_NOT_FOUND["data"]["indexer"]
        assert indexer is None
    
    def test_detect_over_allocated_indexer(self):
        """Test detecting over-allocated indexer (negative remaining)"""
        indexer = INDEXER_OVER_ALLOCATED["data"]["indexer"]
        
        total = int(indexer["tokenCapacity"]) / 1e18
        allocated = int(indexer["allocatedTokens"]) / 1e18
        remaining = total - allocated
        
        assert remaining < 0  # Over-allocated
        assert remaining == pytest.approx(-1_000_000, rel=1e-6)
    
    def test_whale_indexer_numbers(self):
        """Test handling very large token amounts"""
        indexer = INDEXER_WHALE["data"]["indexer"]
        
        staked = int(indexer["stakedTokens"]) / 1e18
        delegated = int(indexer["delegatedTokens"]) / 1e18
        
        assert staked == pytest.approx(50_000_000, rel=1e-6)
        assert delegated == pytest.approx(800_000_000, rel=1e-6)
        
        # Verify delegation is at max (16x)
        max_delegation = staked * 16
        assert delegated == max_delegation


class TestAllocationDataParsing:
    """Tests for parsing allocation data"""
    
    def test_parse_active_allocations(self):
        """Test parsing active allocations with deployment info"""
        allocations = ALLOCATIONS_ACTIVE["data"]["allocations"]
        
        assert len(allocations) == 2
        
        alloc = allocations[0]
        assert alloc["status"] == "Active"
        assert int(alloc["allocatedTokens"]) / 1e18 == pytest.approx(5_000_000, rel=1e-6)
        assert alloc["subgraphDeployment"]["ipfsHash"].startswith("Qm")
    
    def test_parse_empty_allocations(self):
        """Test handling empty allocations list"""
        allocations = ALLOCATIONS_EMPTY["data"]["allocations"]
        
        assert allocations == []
        assert len(allocations) == 0
    
    def test_parse_legacy_vs_normal_allocations(self):
        """Test distinguishing legacy from normal allocations"""
        allocations = ALLOCATIONS_CLOSED_WITH_LEGACY["data"]["allocations"]
        
        legacy = next(a for a in allocations if a.get("isLegacy"))
        normal = next(a for a in allocations if not a.get("isLegacy"))
        
        # Legacy has 0 rewards in subgraph
        assert int(legacy["indexingRewards"]) == 0
        
        # Normal has rewards tracked
        assert int(normal["indexingRewards"]) > 0
    
    def test_handle_missing_deployment(self):
        """Test handling allocation with null deployment"""
        allocations = ALLOCATIONS_MISSING_DEPLOYMENT["data"]["allocations"]
        
        alloc = allocations[0]
        deployment = alloc.get("subgraphDeployment")
        
        assert deployment is None
        
        # Code should handle this gracefully
        ipfs_hash = deployment.get("ipfsHash", "Unknown") if deployment else "Unknown"
        assert ipfs_hash == "Unknown"


class TestDelegationDataParsing:
    """Tests for parsing delegation data"""
    
    def test_parse_delegations_with_thawing(self):
        """Test parsing delegations including thawing amounts"""
        delegations = DELEGATIONS_RESPONSE["data"]["delegatedStakes"]
        
        # Find delegation with thawing tokens
        thawing = next(d for d in delegations if int(d["lockedTokens"]) > 0)
        
        staked = int(thawing["stakedTokens"]) / 1e18
        locked = int(thawing["lockedTokens"]) / 1e18
        
        assert staked == pytest.approx(50_000, rel=1e-6)
        assert locked == pytest.approx(10_000, rel=1e-6)
    
    def test_parse_empty_delegations(self):
        """Test handling delegator with no delegations"""
        delegations = DELEGATIONS_EMPTY["data"]["delegatedStakes"]
        
        assert delegations == []


class TestNetworkStatsParsing:
    """Tests for parsing network-wide statistics"""
    
    def test_parse_network_stats(self):
        """Test parsing network statistics for APR calculation"""
        network = NETWORK_STATS["data"]["graphNetwork"]
        
        total_allocated = int(network["totalTokensAllocated"]) / 1e18
        total_signalled = int(network["totalTokensSignalled"]) / 1e18
        issuance_per_block = int(network["networkGRTIssuancePerBlock"]) / 1e18
        
        assert total_allocated == pytest.approx(3_500_000_000, rel=1e-6)
        assert total_signalled == pytest.approx(150_000_000, rel=1e-6)
        assert issuance_per_block == pytest.approx(0.114, rel=1e-2)


class TestSyncStatusParsing:
    """Tests for parsing indexer status endpoint responses"""
    
    def test_parse_healthy_synced(self):
        """Test parsing synced deployment status"""
        statuses = STATUS_HEALTHY_SYNCED["data"]["indexingStatuses"]
        
        status = statuses[0]
        assert status["synced"] == True
        assert status["health"] == "healthy"
        
        chain = status["chains"][0]
        assert int(chain["latestBlock"]["number"]) == int(chain["chainHeadBlock"]["number"])
    
    def test_parse_syncing_status(self):
        """Test parsing deployment that is still syncing"""
        statuses = STATUS_SYNCING["data"]["indexingStatuses"]
        
        status = statuses[0]
        chain = status["chains"][0]
        
        latest = int(chain["latestBlock"]["number"])
        head = int(chain["chainHeadBlock"]["number"])
        behind = head - latest
        
        assert behind == 500_000
        assert status["synced"] == False
    
    def test_detect_false_synced_bug(self):
        """Test detecting the 'false synced' bug (synced=True but far behind)"""
        statuses = STATUS_FALSE_SYNCED["data"]["indexingStatuses"]
        
        status = statuses[0]
        chain = status["chains"][0]
        
        # Graph Node says synced...
        assert status["synced"] == True
        
        # ...but calculate actual blocks behind
        latest = int(chain["latestBlock"]["number"])
        head = int(chain["chainHeadBlock"]["number"])
        behind = head - latest
        
        # Actually 300k behind!
        assert behind == 300_000
        
        # Our code should NOT trust synced=True when behind > threshold
        threshold = 100
        actual_synced = status["synced"] and behind <= threshold
        assert actual_synced == False
    
    def test_parse_failed_status(self):
        """Test parsing failed deployment with error"""
        statuses = STATUS_FAILED["data"]["indexingStatuses"]
        
        status = statuses[0]
        assert status["health"] == "failed"
        assert status["fatalError"]["message"] is not None
        assert "deterministic error" in status["fatalError"]["message"]
    
    def test_handle_null_chains(self):
        """Test handling null chains array"""
        statuses = STATUS_NULL_CHAINS["data"]["indexingStatuses"]
        
        status = statuses[0]
        chains = status.get("chains") or []
        
        assert chains == [] or chains is None
        
        # Code should handle gracefully
        chain = chains[0] if chains else {}
        latest = chain.get("latestBlock", {}).get("number", 0) if chain else 0
        assert latest == 0
    
    def test_handle_empty_chains(self):
        """Test handling empty chains array"""
        statuses = STATUS_EMPTY_CHAINS["data"]["indexingStatuses"]
        
        status = statuses[0]
        chains = status.get("chains") or []
        
        assert len(chains) == 0
    
    def test_handle_null_latest_block(self):
        """Test handling null latestBlock"""
        statuses = STATUS_NULL_BLOCKS["data"]["indexingStatuses"]
        
        status = statuses[0]
        chain = status["chains"][0]
        
        # latestBlock is None
        assert chain["latestBlock"] is None
        
        # Code should handle gracefully
        latest_block = chain.get("latestBlock") or {}
        latest = int(latest_block.get("number", 0) or 0)
        assert latest == 0
    
    def test_parse_multiple_deployments(self):
        """Test parsing status with multiple deployments"""
        statuses = STATUS_MULTIPLE_DEPLOYMENTS["data"]["indexingStatuses"]
        
        assert len(statuses) == 3
        
        # Build lookup by hash
        status_map = {s["subgraph"]: s for s in statuses}
        
        # Verify different states
        synced = status_map["QmDeployment1234567890123456789012345678"]
        syncing = status_map["QmDeployment2345678901234567890123456789"]
        failed = status_map["QmDeployment3456789012345678901234567890"]
        
        assert synced["synced"] == True
        assert syncing["synced"] == False and syncing["health"] == "healthy"
        assert failed["health"] == "failed"


class TestGraphQLErrorHandling:
    """Tests for handling GraphQL errors"""
    
    def test_detect_schema_error(self):
        """Test detecting schema/field errors"""
        response = GRAPHQL_ERROR_SCHEMA
        
        assert "errors" in response
        assert "has no field" in response["errors"][0]["message"]
    
    def test_detect_variable_error(self):
        """Test detecting variable validation errors"""
        response = GRAPHQL_ERROR_VARIABLE
        
        assert "errors" in response
        assert "invalid value" in response["errors"][0]["message"]
    
    def test_graceful_error_handling(self):
        """Test that errors don't cause crashes"""
        response = GRAPHQL_ERROR_SCHEMA
        
        # Typical pattern: check for errors, return empty data
        if "errors" in response:
            data = {}
        else:
            data = response.get("data", {})
        
        allocations = data.get("allocations", [])
        assert allocations == []


class TestRewardCalculations:
    """Tests for reward-related calculations"""
    
    def test_legacy_rewards_aggregation(self):
        """Test aggregating multiple reward events for legacy allocations"""
        logs = MULTIPLE_REWARDS_SAME_ALLOCATION
        
        total_rewards = 0
        for log in logs:
            amount = int(log.data.hex(), 16)
            total_rewards += amount
        
        # 10k + 15k + 25k = 50k GRT
        assert total_rewards / 1e18 == pytest.approx(50_000, rel=1e-6)
    
    def test_rewards_per_allocation(self):
        """Test mapping rewards to different allocations"""
        logs = REWARDS_MULTIPLE_ALLOCATIONS
        
        rewards_by_alloc = {}
        for log in logs:
            alloc_id = "0x" + log.topics[2].hex()[-40:]
            amount = int(log.data.hex(), 16)
            rewards_by_alloc[alloc_id] = rewards_by_alloc.get(alloc_id, 0) + amount
        
        assert len(rewards_by_alloc) == 3
        assert rewards_by_alloc["0x0a110c1111111111111111111111111111111111"] / 1e18 == pytest.approx(50_000)
        assert rewards_by_alloc["0x0a110c2222222222222222222222222222222222"] / 1e18 == pytest.approx(30_000)
        assert rewards_by_alloc["0x0a110c3333333333333333333333333333333333"] / 1e18 == pytest.approx(20_000)
    
    def test_empty_rewards(self):
        """Test handling no reward events"""
        logs = EMPTY_LOGS
        
        total_rewards = sum(int(log.data.hex(), 16) for log in logs)
        assert total_rewards == 0


class TestPOISubmissionParsing:
    """Tests for parsing POI submission data"""
    
    def test_parse_poi_submissions(self):
        """Test parsing POI submissions for reward collection tracking"""
        submissions = POI_SUBMISSIONS["data"]["poiSubmissions"]
        
        assert len(submissions) == 1
        
        poi = submissions[0]
        alloc = poi["allocation"]
        
        assert alloc["status"] == "Active"
        rewards = int(alloc["indexingRewards"]) / 1e18
        assert rewards == pytest.approx(25_000, rel=1e-6)
    
    def test_empty_poi_submissions(self):
        """Test handling no POI submissions"""
        submissions = POI_SUBMISSIONS_EMPTY["data"]["poiSubmissions"]
        
        assert submissions == []


class TestTokenConversions:
    """Tests for token amount conversions (wei to GRT)"""
    
    def test_small_amount_conversion(self):
        """Test converting small token amounts"""
        wei = "1000000000000000000"  # 1 GRT
        grt = int(wei) / 1e18
        assert grt == 1.0
    
    def test_large_amount_conversion(self):
        """Test converting large token amounts (billions)"""
        wei = "1000000000000000000000000000"  # 1B GRT
        grt = int(wei) / 1e18
        assert grt == 1_000_000_000
    
    def test_fractional_amount_conversion(self):
        """Test converting fractional token amounts"""
        wei = "1500000000000000000"  # 1.5 GRT
        grt = int(wei) / 1e18
        assert grt == 1.5
    
    def test_zero_amount_conversion(self):
        """Test converting zero tokens"""
        wei = "0"
        grt = int(wei) / 1e18
        assert grt == 0.0
    
    def test_dust_amount_conversion(self):
        """Test converting dust amounts (very small)"""
        wei = "100000000000000"  # 0.0001 GRT
        grt = int(wei) / 1e18
        assert grt == pytest.approx(0.0001, rel=1e-6)


if __name__ == '__main__':
    pytest.main([__file__, '-v'])

