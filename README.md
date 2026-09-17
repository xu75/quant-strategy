# Quant Strategy

[![CI](https://github.com/xu75/quant-strategy/actions/workflows/run_strategy.yml/badge.svg)](https://github.com/xu75/quant-strategy/actions/workflows/run_strategy.yml)
[![License: AGPL-3.0](https://img.shields.io/badge/License-AGPL%203.0-blue.svg)](https://opensource.org/licenses/AGPL-3.0)

> **Open strategy research for low-frequency quant trading.**
> Replace emotional trading with deterministic rules. Strategy logic fully transparent.

**🌐 Live Site**: [quant-strategy.mesh-hub.xyz](https://quant-strategy.mesh-hub.xyz)  
**📡 Telegram Signals**: [t.me/meshhubsignal_channel](https://t.me/meshhubsignal_channel)

This is not just another backtesting framework—it's a **live, verifiable signal pipeline**. The execution pipeline is decoupled from individual strategies, with signals automatically generated and published to a static frontend.

## 🌟 Vision & Discipline

- **Fully Automated Pipeline**: GitHub Actions runs every 4 hours. Signals, charts, and backtest results are automatically committed to the repo, triggering a frontend rebuild.
- **Strict Fee Discipline**: All backtests enforce a default 0.1%/side slippage & fee rate. This is our hard constraint for realistic, credible research.
- **Open Knowledge**: No black-box algorithms; strategy rules, parameters, and execution methodology are completely transparent. Anyone can reproduce results using the published rules.

## 📈 Current Strategies

- **[TrendLock 40 (btc_ma_trend)](strategies/btc_ma_trend/manifest.yaml)**: BTC 4H MA240 trend-following strategy with a 4-day minimum hold period. Signals update every 4 hours.

## 🏗️ Architecture

The project is designed with a decoupled, plugin-based architecture:

- **`core/`**: Platform core including the registry, runner, and configuration management.
- **`pipeline/`**: The universal engine for data fetching, backtesting, and reporting. Independent of any specific strategy.
- **`strategies/`**: Strategy plugins. Each defines its metadata in `manifest.yaml` and logic in `signal.py`.
- **`outputs/`**: Research assets, parameter sweep analyses, and evaluation outputs.
- **`site/`**: An Astro-based static frontend that visualizes equity curves, price charts, and current signals based on the generated data.
- **`docs/` & `tests/`**: Comprehensive feature specifications, ADRs, research papers, and a pytest suite ensuring pipeline integrity.

## 📂 Project Structure

```text
quant-strategy/
├── core/                  # Platform core (registry, runner, config)
├── pipeline/              # Backtesting and reporting engine
├── strategies/            # Strategy plugins (manifest.yaml, signal.py)
├── data/                  # Auto-generated strategy outputs (namespaced by ID)
├── outputs/               # Parameter sweep analysis & research artifacts
├── site/                  # Astro SSG frontend (port 3003)
├── tests/                 # pytest test suite
└── docs/                  # Feature specs, ADRs, research docs
```

## 🛠️ Getting Started

### Backend (Strategy Engine)
Tested on Python 3.12 (Requires 3.10+).

1. Install the project and dependencies:
   ```bash
   pip install -e ".[dev]"
   ```
2. Verify the installation:
   ```bash
   python -m pytest
   ```
3. Run the strategy pipeline or parameter sweeps:
   ```bash
   python run_strategy.py
   python sweep_btc_ma.py
   ```

### Frontend (Signal Visualization Site)
Requires Node.js and pnpm.

1. Navigate to the site directory:
   ```bash
   cd site
   ```
2. Install dependencies & build:
   ```bash
   pnpm install
   pnpm build
   ```
3. Start the development server:
   ```bash
   pnpm dev
   ```

## ⚠️ Disclaimer
**This project is for educational and research purposes only.** The strategies, signals, and research documents provided do not constitute financial or investment advice.

## 📜 License
This project is licensed under the **AGPL-3.0-or-later**.
