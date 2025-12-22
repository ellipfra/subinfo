#!/usr/bin/env python3
"""
Contract addresses and constants for The Graph on Arbitrum One

This module centralizes all smart contract addresses and function selectors
used by grtinfo CLI tools.
"""

# =============================================================================
# Contract Addresses (Arbitrum One)
# =============================================================================

# Core Graph Protocol contracts
REWARDS_MANAGER = "0x971B9d3d0Ae3ECa029CAB5eA1fB0F72c85e6a525"
STAKING = "0x00669A4CF01450B64E8A2A20E9b1fcb71E61eF03"
SUBGRAPH_SERVICE = "0xB2Bb92D0dE618878e438b55d5846cFEcD9301105"
GRT_TOKEN = "0x9623063377AD1B27544C965cCd7342f7EA7e88C7"

# Legacy/alternate names for compatibility
STAKING_CONTRACT = STAKING
REWARDS_CONTRACT = REWARDS_MANAGER


# =============================================================================
# Event Topics (keccak256 hashes of event signatures)
# =============================================================================

# HorizonRewardAssigned(address indexed indexer, address indexed allocationID, uint256 amount)
HORIZON_REWARD_ASSIGNED_TOPIC = "0xa111914d7f2ea8beca61d12f1a1f38c5533de5f1823c3936422df4404ac2ec68"


# =============================================================================
# Function Selectors (first 4 bytes of keccak256 of function signature)
# =============================================================================

# RewardsManager.getRewards(address _rewardsIssuer, address _allocationID) returns (uint256)
GET_REWARDS_SELECTOR = "0x0e6f0a5e"

# Staking.getDelegation(address _indexer, address _delegator) returns (uint256 shares, uint256 tokensLocked, uint256 tokensLockedUntil)
GET_DELEGATION_SELECTOR = "0x15049a5a"


# =============================================================================
# Network Constants
# =============================================================================

# GRT token decimals
GRT_DECIMALS = 18

# PPM (parts per million) - used for reward cuts
PPM_BASE = 1_000_000

# Default thawing period in epochs (approximately 28 days)
DEFAULT_THAWING_PERIOD = 28

# Epoch duration in seconds (approximately 24 hours on Arbitrum)
EPOCH_DURATION_SECONDS = 86400  # 24 hours

# Maximum allocation age in epochs before rewards expire
MAX_ALLOCATION_EPOCHS = 28


# =============================================================================
# Helper Functions
# =============================================================================

def to_checksum_address(address: str) -> str:
    """Convert address to checksum format (simple implementation)
    
    For proper checksum, use web3.Web3.to_checksum_address()
    This is a fallback that just ensures 0x prefix and lowercase.
    """
    addr = address.lower()
    if not addr.startswith('0x'):
        addr = '0x' + addr
    return addr


def pad_address(address: str) -> str:
    """Pad address to 32 bytes for use in event topic filters
    
    Args:
        address: Ethereum address (with or without 0x prefix)
    
    Returns:
        0x-prefixed 64-character hex string (32 bytes)
    """
    addr = address.lower()
    if addr.startswith('0x'):
        addr = addr[2:]
    return '0x' + addr.zfill(64)

