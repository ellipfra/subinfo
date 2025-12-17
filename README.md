# subinfo

CLI tools to analyze TheGraph Network indexers, allocations and curation signals.

## Tools

### `subinfo` - Subgraph Analysis

Analyze allocations and curation signals for a specific subgraph deployment.

**Features:**
- **Subgraph Metadata**: Network, reward proportion vs network average
- **Curation Signal**: Current signal amount
- **Signal Changes**: Recent signal additions/removals, upgrade detection
- **Active Allocations**: Current indexer allocations with duration
- **Accrued Rewards**: Precise rewards calculation from smart contract for your allocations
- **Allocation Timeline**: Chronological view of allocations/unallocations/collections
- **ENS Resolution**: Automatically resolves indexer addresses to ENS names
- **Clickable Links**: Qm hashes link to The Graph Explorer (in compatible terminals)

### `indexerinfo` - Indexer Analysis

Display detailed information about any indexer.

**Features:**
- **Stake Information**: Self stake, delegated stake, allocation status, delegation room
- **Reward Cuts**: Raw and effective cuts for indexing rewards and query fees
- **Activity Timeline**: Recent allocations, unallocations, reward collections, delegations
- **Top Allocations**: Largest active allocations with signal info
- **Flexible Search**: Find indexers by partial ENS name, address, or URL
- **Clickable Links**: Qm hashes link to The Graph Explorer

## Installation

```bash
# Clone the repository
git clone https://github.com/ellipfra/subinfo.git
cd subinfo

# Run the installation script
./install.sh

# Or install manually
pip3 install -r requirements.txt
pip3 install -e .
```

## Configuration

Configure your TheGraph Network subgraph URL before using the tools.

### Option 1: Environment Variables

```bash
export THEGRAPH_NETWORK_SUBGRAPH_URL="https://your-graph-node/subgraphs/id/QmNetworkSubgraphHash"
export MY_INDEXER_ID="0xYourIndexerAddress"  # Optional: for highlighting your allocations
export ENS_SUBGRAPH_URL="https://your-graph-node/subgraphs/id/QmENSSubgraphHash"  # Optional
```

### Option 2: Configuration File

Edit `~/.subinfo/config.json`:

```json
{
  "network_subgraph_url": "https://your-graph-node/subgraphs/id/QmNetworkSubgraphHash",
  "my_indexer_id": "0xYourIndexerAddress",
  "ens_subgraph_url": "https://your-graph-node/subgraphs/id/QmENSSubgraphHash"
}
```

## Usage

### subinfo

```bash
subinfo <subgraph_hash>
```

**Options:**
- `--url URL`: Override the network subgraph URL
- `--hours N`: Number of hours for history (default: 48)

**Example:**

```bash
subinfo QmYourSubgraphHash
```

**Output:**

```
Subgraph: QmYourSubgraphHash

▸ Subgraph Metadata
Network: mainnet
Reward Proportion: 150.25%

▸ Curation Signal
Total signal: 10,000 GRT

▸ Signal Changes (48h)
  [+]  2025-12-12 08:26  0xabcd1234...              500 GRT

▸ Active Allocations
  ★  my-indexer.eth (0x1234..)        100,000 GRT  2025-12-11 12:00  Active (1d 2h)
     other-indexer.eth (0x5678..)      50,000 GRT  2025-12-10 08:00  Active (2d 6h)
Total: 150,000 GRT
Your Allocation:
  ★ Allocated: 100,000 GRT for 1.2 days
  ★ Accrued rewards: 125.50 GRT
  ★ Indexer share (10.0%): 12.55 GRT
  ★ Delegator share (90.0%): 112.95 GRT

▸ Allocations/Unallocations Timeline (48h)
  [+] ★  2025-12-11 12:00  my-indexer.eth (0x1234..)       100,000 GRT  Active
  [-]    2025-12-10 18:00  leaving-indexer (0x9abc..)       25,000 GRT  closed → 150 GRT ⚠ 45% unallocated
  [$] ★  2025-12-10 12:00  my-indexer.eth (0x1234..)       100,000 GRT  Collect
Total: +100,000 GRT | -25,000 GRT
```

### indexerinfo

```bash
indexerinfo <search_term>
```

**Search by:**
- Partial ENS name: `ellipfra`, `pinax`
- Partial address: `0xf92f`, `f92f430`
- Partial URL: `staked.cloud`

**Options:**
- `--hours N`: Number of hours for activity history (default: 48)

**Example:**

```bash
indexerinfo ellipfra
```

**Output:**

```
Indexer: ellipfra-indexer.eth (0xf92f430dd8567b0d466358c79594ab58d919a6d4)
https://graph-l2prod.ellipfra.com/

▸ Stake
  Self stake:      7,500,862 GRT
  Delegated:       108,751,231 GRT / 120,013,796 GRT (91%)
  Delegation room: 11,262,564 GRT
  Total:           116,252,094 GRT
  Allocated:       113,046,962 GRT
  Remaining:       3,205,132 GRT (2.8%)

▸ Reward Cuts
  Indexing rewards: 26.5% raw, 21.4% effective on delegators
  Query fees:       90.0% raw, 89.3% effective on delegators

▸ Allocations
  Active: 839 | Total: 0

▸ Activity (48h)
  [-] 2025-12-17 08:51  QmREtzkASBqSARbFa3QAUQ2NkSgpZVvMaSbDnaP57WQiki  50k GRT → 123 GRT
  [+] 2025-12-16 14:30  QmXyz123...                                       100k GRT
  [↑] 2025-12-16 10:00  0xdelegator123...                                 +5,000 GRT delegated

▸ Top Active Allocations
  QmasYjypV6nTLp4iNH4Vjf7fksRNxAkAskqDdKf2DCsQkV  2,199,822 GRT    7d 11h  signal: 38,983
  QmV7EeFsKQDurfwTakfj9K77UuQVQ6cUtnPUQ2wfYcpXUa  1,649,209 GRT    7d 11h  signal: 29,580
```

## Legend

| Symbol | Meaning |
|--------|---------|
| `★` | Your indexer (configured via `MY_INDEXER_ID`) |
| `[+]` | Allocation created |
| `[-]` | Allocation closed (unallocation) |
| `[$]` | Reward collection (POI submitted) |
| `[↑]` | Subgraph upgrade / Delegation received |
| `[↓]` | Undelegation |
| `⚠ XX% unallocated` | Warning: indexer has high unallocated stake |

## Terminal Compatibility

Qm hashes are rendered as clickable hyperlinks using OSC 8 escape sequences. This works in:
- iTerm2
- kitty
- GNOME Terminal
- Windows Terminal
- Hyper
- Most modern terminal emulators

## License

MIT License - see [LICENSE](LICENSE) file.
