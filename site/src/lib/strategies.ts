import fs from 'node:fs';
import path from 'node:path';
import yaml from 'js-yaml';

export interface StrategyManifest {
  id: string;
  name: string;
  version: string;
  description: string;
  config: Record<string, any>;
  display: { slug: string; category: string; short_desc: string };
  launch_date: string;
  enabled: boolean;
  status?: string;
  status_reason?: string;
  status_date?: string;
  superseded_by?: string;
  research?: {
    paper_title: string;
    website_path: string;
    canonical_doc?: string;
    source_project?: string;
    source_docx?: string;
  };
}

export interface StrategyData {
  manifest: StrategyManifest;
  latest: any;
  backtest: any;
  hasData: boolean;
}

const STRATEGIES_DIR = path.resolve(process.cwd(), '../strategies');
const DATA_DIR = path.resolve(process.cwd(), '../data');

export function discoverStrategies(): StrategyManifest[] {
  const entries = fs.readdirSync(STRATEGIES_DIR, { withFileTypes: true });
  const manifests: StrategyManifest[] = [];

  for (const entry of entries) {
    if (!entry.isDirectory()) continue;
    const manifestPath = path.join(STRATEGIES_DIR, entry.name, 'manifest.yaml');
    if (!fs.existsSync(manifestPath)) continue;
    const manifest = yaml.load(fs.readFileSync(manifestPath, 'utf-8')) as StrategyManifest;
    manifests.push(manifest);
  }

  return manifests;
}

export function loadStrategyData(strategyId: string): StrategyData {
  const manifestPath = path.join(STRATEGIES_DIR, strategyId, 'manifest.yaml');
  const manifest = yaml.load(fs.readFileSync(manifestPath, 'utf-8')) as StrategyManifest;

  const dataDir = path.join(DATA_DIR, strategyId);
  const latestPath = path.join(dataDir, 'latest.json');
  const backtestPath = path.join(dataDir, 'backtest.json');
  const hasData = fs.existsSync(latestPath) && fs.existsSync(backtestPath);

  const latest = hasData ? JSON.parse(fs.readFileSync(latestPath, 'utf-8')) : null;
  const backtest = hasData ? JSON.parse(fs.readFileSync(backtestPath, 'utf-8')) : null;

  return { manifest, latest, backtest, hasData };
}

export function loadAllStrategies(): StrategyData[] {
  return discoverStrategies().map(m => loadStrategyData(m.id));
}

export function getStrategyType(manifest: StrategyManifest): 'ma-trend' | 'echotrend' {
  const id = manifest.id;
  if (id.startsWith('echotrend')) return 'echotrend';
  return 'ma-trend';
}
