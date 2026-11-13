# Bundled report assets

Pinned browser distributions for offline reports. No runtime CDN or package installation.

- cytoscape 3.35.0: https://registry.npmjs.org/cytoscape/-/cytoscape-3.35.0.tgz; file `cytoscape.min.js`; SHA256 `71fa9ebec21a7ca2c40cff6aacc30d1f6464e363662ad4b2c197e1adc770cd94`.

- elkjs 0.12.1: https://registry.npmjs.org/elkjs/-/elkjs-0.12.1.tgz; file `elk.bundled.js`; SHA256 `b1032b7cfd2a57c54c6f0e8369d7665e19a579954f7cd20a6dae28f1912ea0d7`; EPL-2.0 license in `elk-LICENSE.md`. Browser bundle runs with its in-process worker, with no remote worker/CDN.

- echarts 5.6.0: https://registry.npmjs.org/echarts/-/echarts-5.6.0.tgz; file `echarts.min.js`; SHA256 `bf4a223524e40b77c304bec67e1222cf551f14880cf42c69dc046558e11c07b1`.

`graph-layout.js` owns neutral grouped placement; `graph-view.js` owns rendering and interaction.
