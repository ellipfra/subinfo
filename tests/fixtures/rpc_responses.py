#!/usr/bin/env python3
"""
Mock RPC responses for web3/eth_getLogs calls

These fixtures represent realistic responses from Arbitrum One RPC endpoints,
including edge cases for reward events.
"""

from unittest.mock import MagicMock


def create_mock_log(indexer: str, allocation_id: str, amount_wei: int, block_number: int):
    """Helper to create a mock log entry for HorizonRewardAssigned event"""
    # Pad addresses to 32 bytes (64 hex chars)
    indexer_topic = "0x" + indexer[2:].lower().zfill(64)
    alloc_topic = "0x" + allocation_id[2:].lower().zfill(64)
    
    # Amount is encoded as uint256 in data field
    amount_hex = hex(amount_wei)[2:].zfill(64)
    
    log = MagicMock()
    log.topics = [
        bytes.fromhex("a111914d7f2ea8beca61d12f1a1f38c5533de5f1823c3936422df4404ac2ec68"),  # event sig
        bytes.fromhex(indexer_topic[2:]),
        bytes.fromhex(alloc_topic[2:])
    ]
    log.data = bytes.fromhex(amount_hex)
    log.blockNumber = block_number
    
    return log


# =============================================================================
# REWARD EVENT LOGS
# =============================================================================

# Single reward event
SINGLE_REWARD_LOG = [
    create_mock_log(
        indexer="0xf92f430dd8567b0d466358c79594ab58d919a6d4",
        allocation_id="0xa110c1111111111111111111111111111111111",
        amount_wei=50000000000000000000000,  # 50k GRT
        block_number=250000000
    )
]

# Multiple rewards for same allocation (multiple POI submissions)
MULTIPLE_REWARDS_SAME_ALLOCATION = [
    create_mock_log(
        indexer="0xf92f430dd8567b0d466358c79594ab58d919a6d4",
        allocation_id="0xa110c1111111111111111111111111111111111",
        amount_wei=10000000000000000000000,  # 10k GRT - first collection
        block_number=249000000
    ),
    create_mock_log(
        indexer="0xf92f430dd8567b0d466358c79594ab58d919a6d4",
        allocation_id="0xa110c1111111111111111111111111111111111",
        amount_wei=15000000000000000000000,  # 15k GRT - second collection
        block_number=249500000
    ),
    create_mock_log(
        indexer="0xf92f430dd8567b0d466358c79594ab58d919a6d4",
        allocation_id="0xa110c1111111111111111111111111111111111",
        amount_wei=25000000000000000000000,  # 25k GRT - third collection
        block_number=250000000
    ),
]

# Rewards for multiple allocations
REWARDS_MULTIPLE_ALLOCATIONS = [
    create_mock_log(
        indexer="0xf92f430dd8567b0d466358c79594ab58d919a6d4",
        allocation_id="0xa110c1111111111111111111111111111111111",
        amount_wei=50000000000000000000000,  # 50k GRT
        block_number=250000000
    ),
    create_mock_log(
        indexer="0xf92f430dd8567b0d466358c79594ab58d919a6d4",
        allocation_id="0xa110c2222222222222222222222222222222222",
        amount_wei=30000000000000000000000,  # 30k GRT
        block_number=250000100
    ),
    create_mock_log(
        indexer="0xf92f430dd8567b0d466358c79594ab58d919a6d4",
        allocation_id="0xa110c3333333333333333333333333333333333",
        amount_wei=20000000000000000000000,  # 20k GRT
        block_number=250000200
    ),
]

# Empty logs (no rewards found)
EMPTY_LOGS = []

# Very large reward (whale allocation)
WHALE_REWARD_LOG = [
    create_mock_log(
        indexer="0x1a1e00000000000000000000000000000000000",
        allocation_id="0x1a1ea110c000000000000000000000000000",
        amount_wei=5000000000000000000000000,  # 5M GRT!
        block_number=250000000
    )
]

# Tiny reward (dust)
DUST_REWARD_LOG = [
    create_mock_log(
        indexer="0xd057000000000000000000000000000000000",
        allocation_id="0xd057a110c000000000000000000000000000",
        amount_wei=100000000000000000,  # 0.1 GRT
        block_number=250000000
    )
]


# =============================================================================
# MULTICALL RESPONSES (for accrued rewards)
# =============================================================================

def create_multicall_result(amounts_wei: list):
    """Create a mock multicall response with multiple reward amounts"""
    results = []
    for amount in amounts_wei:
        # ABI encode uint256
        encoded = "0x" + hex(amount)[2:].zfill(64)
        results.append(bytes.fromhex(encoded[2:]))
    return results


MULTICALL_SINGLE_REWARD = create_multicall_result([25000000000000000000000])  # 25k GRT

MULTICALL_MULTIPLE_REWARDS = create_multicall_result([
    25000000000000000000000,   # 25k GRT
    50000000000000000000000,   # 50k GRT  
    10000000000000000000000,   # 10k GRT
])

MULTICALL_ZERO_REWARDS = create_multicall_result([0, 0, 0])

MULTICALL_MIXED_REWARDS = create_multicall_result([
    100000000000000000000000,  # 100k GRT
    0,                          # No rewards yet
    5000000000000000000000,     # 5k GRT
])


# =============================================================================
# INDEXER STATUS ENDPOINT RESPONSES
# =============================================================================

STATUS_HEALTHY_SYNCED = {
    "data": {
        "indexingStatuses": [
            {
                "subgraph": "QmVNvcHZoQYys9tJijMJxiiL5zGA7gL1B7mLFGRqypydgC",
                "synced": True,
                "health": "healthy",
                "fatalError": None,
                "chains": [
                    {
                        "network": "base",
                        "latestBlock": {"number": "25000000"},
                        "chainHeadBlock": {"number": "25000000"}
                    }
                ]
            }
        ]
    }
}

STATUS_SYNCING = {
    "data": {
        "indexingStatuses": [
            {
                "subgraph": "QmSyncingSubgraph123456789012345678901234",
                "synced": False,
                "health": "healthy",
                "fatalError": None,
                "chains": [
                    {
                        "network": "mainnet",
                        "latestBlock": {"number": "19500000"},
                        "chainHeadBlock": {"number": "20000000"}
                    }
                ]
            }
        ]
    }
}

STATUS_FALSE_SYNCED = {
    "data": {
        "indexingStatuses": [
            {
                "subgraph": "QmFalseSynced1234567890123456789012345",
                "synced": True,  # Says synced...
                "health": "healthy",
                "fatalError": None,
                "chains": [
                    {
                        "network": "mainnet",
                        "latestBlock": {"number": "19700000"},
                        "chainHeadBlock": {"number": "20000000"}  # ...but 300k behind!
                    }
                ]
            }
        ]
    }
}

STATUS_FAILED = {
    "data": {
        "indexingStatuses": [
            {
                "subgraph": "QmFailedSubgraph12345678901234567890123",
                "synced": False,
                "health": "failed",
                "fatalError": {"message": "Block processing failed: deterministic error"},
                "chains": [
                    {
                        "network": "arbitrum-one",
                        "latestBlock": {"number": "150000000"},
                        "chainHeadBlock": {"number": "250000000"}
                    }
                ]
            }
        ]
    }
}

STATUS_NULL_CHAINS = {
    "data": {
        "indexingStatuses": [
            {
                "subgraph": "QmNullChains123456789012345678901234567",
                "synced": False,
                "health": "healthy",
                "fatalError": None,
                "chains": None
            }
        ]
    }
}

STATUS_EMPTY_CHAINS = {
    "data": {
        "indexingStatuses": [
            {
                "subgraph": "QmEmptyChains12345678901234567890123456",
                "synced": False,
                "health": "healthy",
                "fatalError": None,
                "chains": []
            }
        ]
    }
}

STATUS_NULL_BLOCKS = {
    "data": {
        "indexingStatuses": [
            {
                "subgraph": "QmNullBlocks123456789012345678901234567",
                "synced": False,
                "health": "healthy",
                "fatalError": None,
                "chains": [
                    {
                        "network": "mainnet",
                        "latestBlock": None,
                        "chainHeadBlock": {"number": "20000000"}
                    }
                ]
            }
        ]
    }
}

STATUS_MULTIPLE_DEPLOYMENTS = {
    "data": {
        "indexingStatuses": [
            {
                "subgraph": "QmDeployment1234567890123456789012345678",
                "synced": True,
                "health": "healthy",
                "fatalError": None,
                "chains": [{"network": "mainnet", "latestBlock": {"number": "20000000"}, "chainHeadBlock": {"number": "20000000"}}]
            },
            {
                "subgraph": "QmDeployment2345678901234567890123456789",
                "synced": False,
                "health": "healthy",
                "fatalError": None,
                "chains": [{"network": "arbitrum-one", "latestBlock": {"number": "249000000"}, "chainHeadBlock": {"number": "250000000"}}]
            },
            {
                "subgraph": "QmDeployment3456789012345678901234567890",
                "synced": False,
                "health": "failed",
                "fatalError": {"message": "WASM error"},
                "chains": [{"network": "polygon", "latestBlock": {"number": "50000000"}, "chainHeadBlock": {"number": "60000000"}}]
            }
        ]
    }
}

STATUS_EMPTY = {
    "data": {
        "indexingStatuses": []
    }
}

STATUS_GRAPHQL_ERROR = {
    "errors": [
        {"message": "Unknown type: indexingStatuses"}
    ]
}

