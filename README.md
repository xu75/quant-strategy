# Quant Strategy

> Open-source low-frequency quant research lab. All free, all open source.

An extensible, open-source quantitative trading strategy platform. It decouples the core execution pipeline from individual trading strategies and provides a static site generator (SSG) frontend for visualizing trading signals and backtest reports.

## 🌟 Vision
All strategies and signals are 100% open-source and free. We believe in the "open-source + donation" model rather than paid signal memberships or black-box trading.

## 🏗️ Architecture
The project is designed with a decoupled, plugin-based architecture:

- **`core/` & `pipeline/`**: The universal engine for data fetching, backtesting, and reporting. Independent of any specific strategy.
- **`strategies/`**: Strategy plugins (e.g., `btc_ma_trend`). Each strategy defines its own `manifest.yaml` and a standard `signal.py` interface.
- **`site/`**: An Astro-based static frontend that dynamically generates pages for all registered strategies, visualizing equity curves, price charts, and current signals.

## 🚀 Built With
- **Backend/Data**: Python 3.10+, Pandas, Matplotlib
- **Frontend**: Astro, TailwindCSS
- **Deployment**: Vercel (Frontend), GitHub Actions (Automated strategy execution & data updates)

## 📂 Project Structure
```text
quant-strategy/
├── core/                  # Platform core (registry, runner, config)
├── pipeline/              # Backtesting and reporting engine
├── strategies/            # Strategy plugins (manifest.yaml, signal.py)
├── data/                  # Generated strategy outputs (namespaced by strategy ID)
├── site/                  # Astro frontend for signal visualization
└── docs/                  # Features specs, ADRs, and discussions
```

## 🛠️ Getting Started

### Backend (Strategy Engine)
Requires Python 3.10+.

1. Install the project and its dependencies:
   ```bash
   pip install -e .
   # or for development: pip install -e ".[dev]"
   ```
2. Run strategies:
   ```bash
   python run_strategy.py
   ```

### Frontend (Signal Visualization Site)
Requires Node.js and pnpm.

1. Navigate to the site directory:
   ```bash
   cd site
   ```
2. Install dependencies:
   ```bash
   pnpm install
   ```
3. Start the development server:
   ```bash
   pnpm dev
   ```

## 📜 License
This project is licensed under the **AGPL-3.0-or-later**.
