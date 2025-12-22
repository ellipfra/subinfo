#!/usr/bin/env python3
"""
Mock GraphQL responses from The Graph Network subgraph

These fixtures represent realistic responses that the tools might receive,
including edge cases and potential issues.
"""

# =============================================================================
# INDEXER RESPONSES
# =============================================================================

INDEXER_FULL_RESPONSE = {
    "data": {
        "indexer": {
            "id": "0xf92f430dd8567b0d466358c79594ab58d919a6d4",
            "url": "https://graph-l2prod.ellipfra.com/",
            "stakedTokens": "7500862000000000000000000",      # 7.5M GRT
            "delegatedTokens": "109547464000000000000000000", # 109.5M GRT
            "delegatedCapacity": "107604299000000000000000000",
            "allocatedTokens": "114429874000000000000000000", # 114.4M GRT
            "availableStake": "245284000000000000000000",
            "tokenCapacity": "114675158000000000000000000",
            "lockedTokens": "0",
            "unstakedTokens": "0",
            "indexingRewardCut": "265000",  # 26.5% in PPM
            "queryFeeCut": "900000",        # 90% in PPM
            "indexingRewardEffectiveCut": "215000",
            "queryFeeEffectiveCut": "893000",
            "delegatorShares": "107604299000000000000000000",
            "delegatorIndexingRewards": "29004382000000000000000000",
            "delegatorQueryFees": "0",
            "delegationExchangeRate": "1.074521345678901234",
            "allocationCount": 45,
            "totalAllocationCount": 1250,
            "createdAt": "1634567890"
        }
    }
}

INDEXER_MINIMAL_RESPONSE = {
    "data": {
        "indexer": {
            "id": "0x1234567890abcdef1234567890abcdef12345678",
            "stakedTokens": "1000000000000000000000",  # 1k GRT
            "delegatedTokens": "0",
            "allocatedTokens": "0"
        }
    }
}

INDEXER_WITH_NULL_URL = {
    "data": {
        "indexer": {
            "id": "0x1234567890abcdef1234567890abcdef12345678",
            "url": None,
            "stakedTokens": "1000000000000000000000",
            "delegatedTokens": "5000000000000000000000",
            "allocatedTokens": "4000000000000000000000"
        }
    }
}

INDEXER_NOT_FOUND = {
    "data": {
        "indexer": None
    }
}

INDEXER_OVER_ALLOCATED = {
    "data": {
        "indexer": {
            "id": "0xoveralloc000000000000000000000000000000",
            "stakedTokens": "1000000000000000000000000",     # 1M GRT
            "delegatedTokens": "5000000000000000000000000",  # 5M GRT
            "tokenCapacity": "6000000000000000000000000",    # 6M total
            "allocatedTokens": "7000000000000000000000000",  # 7M allocated (over!)
            "availableStake": "-1000000000000000000000000"   # -1M available
        }
    }
}

INDEXER_WHALE = {
    "data": {
        "indexer": {
            "id": "0xwhale00000000000000000000000000000000",
            "stakedTokens": "50000000000000000000000000",     # 50M GRT
            "delegatedTokens": "800000000000000000000000000", # 800M GRT (max delegation)
            "allocatedTokens": "850000000000000000000000000", # 850M allocated
            "indexingRewardCut": "100000",  # 10%
        }
    }
}


# =============================================================================
# ALLOCATION RESPONSES
# =============================================================================

ALLOCATIONS_ACTIVE = {
    "data": {
        "allocations": [
            {
                "id": "0xalloc1111111111111111111111111111111111",
                "allocatedTokens": "5000000000000000000000000",  # 5M GRT
                "createdAt": "1703001600",  # Recent
                "status": "Active",
                "subgraphDeployment": {
                    "ipfsHash": "QmVNvcHZoQYys9tJijMJxiiL5zGA7gL1B7mLFGRqypydgC",
                    "signalledTokens": "50000000000000000000000",
                    "stakedTokens": "10000000000000000000000000",
                    "versions": [{"subgraph": {"id": "0xsubgraph1"}}]
                }
            },
            {
                "id": "0xalloc2222222222222222222222222222222222",
                "allocatedTokens": "2000000000000000000000000",  # 2M GRT
                "createdAt": "1702396800",
                "status": "Active",
                "subgraphDeployment": {
                    "ipfsHash": "QmXYZ123456789abcdefghijklmnopqrstuvwxyz",
                    "signalledTokens": "100000000000000000000000",
                    "stakedTokens": "5000000000000000000000000",
                    "versions": []
                }
            }
        ]
    }
}

ALLOCATIONS_EMPTY = {
    "data": {
        "allocations": []
    }
}

ALLOCATIONS_CLOSED_WITH_LEGACY = {
    "data": {
        "allocations": [
            {
                "id": "0xlegacy11111111111111111111111111111111",
                "allocatedTokens": "1000000000000000000000000",
                "createdAt": "1690000000",
                "closedAt": "1703001600",
                "status": "Closed",
                "indexingRewards": "0",  # Legacy: rewards not tracked in subgraph
                "isLegacy": True,
                "subgraphDeployment": {
                    "ipfsHash": "QmLegacySubgraph123456789012345678901234",
                    "signalledTokens": "10000000000000000000000",
                    "versions": []
                }
            },
            {
                "id": "0xnormal11111111111111111111111111111111",
                "allocatedTokens": "2000000000000000000000000",
                "createdAt": "1700000000",
                "closedAt": "1703001600",
                "status": "Closed",
                "indexingRewards": "50000000000000000000000",  # 50k GRT rewards
                "isLegacy": False,
                "subgraphDeployment": {
                    "ipfsHash": "QmNormalSubgraph12345678901234567890123",
                    "signalledTokens": "20000000000000000000000",
                    "versions": []
                }
            }
        ]
    }
}

ALLOCATIONS_MISSING_DEPLOYMENT = {
    "data": {
        "allocations": [
            {
                "id": "0xbadalloc1111111111111111111111111111111",
                "allocatedTokens": "1000000000000000000000000",
                "createdAt": "1703001600",
                "status": "Active",
                "subgraphDeployment": None
            }
        ]
    }
}


# =============================================================================
# DELEGATION RESPONSES
# =============================================================================

DELEGATIONS_RESPONSE = {
    "data": {
        "delegatedStakes": [
            {
                "id": "0xdelegation1-indexer1",
                "delegator": {"id": "0xdelegator111111111111111111111111111111"},
                "indexer": {"id": "0xindexer1111111111111111111111111111111"},
                "stakedTokens": "100000000000000000000000",  # 100k GRT
                "lockedTokens": "0",
                "createdAt": "1690000000",
                "lastDelegatedAt": "1703001600",
                "lastUndelegatedAt": "0"
            },
            {
                "id": "0xdelegation1-indexer2",
                "delegator": {"id": "0xdelegator111111111111111111111111111111"},
                "indexer": {"id": "0xindexer2222222222222222222222222222222"},
                "stakedTokens": "50000000000000000000000",  # 50k GRT
                "lockedTokens": "10000000000000000000000",   # 10k thawing
                "createdAt": "1680000000",
                "lastDelegatedAt": "1700000000",
                "lastUndelegatedAt": "1703001600"
            }
        ]
    }
}

DELEGATIONS_EMPTY = {
    "data": {
        "delegatedStakes": []
    }
}


# =============================================================================
# NETWORK STATS RESPONSES
# =============================================================================

NETWORK_STATS = {
    "data": {
        "graphNetwork": {
            "totalTokensAllocated": "3500000000000000000000000000",  # 3.5B GRT
            "totalTokensSignalled": "150000000000000000000000000",   # 150M GRT
            "networkGRTIssuancePerBlock": "114155251141552511"       # ~0.114 GRT/block
        }
    }
}


# =============================================================================
# SUBGRAPH DEPLOYMENT RESPONSES
# =============================================================================

SUBGRAPH_DEPLOYMENT_FOUND = {
    "data": {
        "subgraphDeployments": [
            {
                "id": "0xdeployment123456789012345678901234567890",
                "ipfsHash": "QmVNvcHZoQYys9tJijMJxiiL5zGA7gL1B7mLFGRqypydgC",
                "signalAmount": "50000000000000000000000",
                "stakedTokens": "10000000000000000000000000",
                "createdAt": "1650000000",
                "deniedAt": "0",
                "indexingRewardAmount": "1000000000000000000000000",
                "manifest": {"network": "base"}
            }
        ]
    }
}

SUBGRAPH_DEPLOYMENT_NOT_FOUND = {
    "data": {
        "subgraphDeployments": []
    }
}


# =============================================================================
# ERROR RESPONSES
# =============================================================================

GRAPHQL_ERROR_SCHEMA = {
    "errors": [
        {
            "message": "Type `Allocation` has no field `nonExistentField`",
            "locations": [{"line": 5, "column": 17}]
        }
    ]
}

GRAPHQL_ERROR_VARIABLE = {
    "errors": [
        {
            "message": "Variable `$id` got invalid value null",
            "locations": [{"line": 1, "column": 7}]
        }
    ]
}

GRAPHQL_ERROR_TIMEOUT = {
    "errors": [
        {
            "message": "Query timed out"
        }
    ]
}


# =============================================================================
# POI SUBMISSIONS
# =============================================================================

POI_SUBMISSIONS = {
    "data": {
        "poiSubmissions": [
            {
                "id": "0xpoi111111111111111111111111111111111111",
                "presentedAtTimestamp": "1703001600",
                "poi": "0xpoi_hash_here",
                "allocation": {
                    "id": "0xalloc1111111111111111111111111111111111",
                    "status": "Active",
                    "indexer": {"id": "0xindexer1111111111111111111111111111111"},
                    "allocatedTokens": "5000000000000000000000000",
                    "indexingRewards": "25000000000000000000000",  # 25k GRT
                    "createdAt": "1700000000"
                }
            }
        ]
    }
}

POI_SUBMISSIONS_EMPTY = {
    "data": {
        "poiSubmissions": []
    }
}

