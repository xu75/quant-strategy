import { defineConfig } from 'astro/config';
import tailwindcss from '@tailwindcss/vite';
import sitemap from '@astrojs/sitemap';

export default defineConfig({
  vite: {
    plugins: [tailwindcss()],
  },
  site: 'https://quant-strategy.mesh-hub.xyz',
  integrations: [
    sitemap({
      changefreq: 'daily',
      priority: 0.7,
      lastmod: new Date(),
      serialize(item) {
        // Homepage gets highest priority
        if (item.url === 'https://quant-strategy.mesh-hub.xyz/') {
          item.priority = 1.0;
          item.changefreq = 'daily';
        }
        // Strategy pages updated daily with real-time signals
        else if (item.url.includes('/strategy/')) {
          item.priority = 0.9;
          item.changefreq = 'daily';
        }
        // Backtest pages less frequent updates
        else if (item.url.includes('/backtest/')) {
          item.priority = 0.8;
          item.changefreq = 'weekly';
        }
        // Research pages are relatively static
        else if (item.url.includes('/research/')) {
          item.priority = 0.7;
          item.changefreq = 'monthly';
        }
        return item;
      },
    }),
  ],
});
