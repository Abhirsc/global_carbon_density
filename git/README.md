---
title: Global Carbon Density
emoji: 🌍
colorFrom: green
colorTo: blue
sdk: docker
app_port: 7860
pinned: false
license: apache-2.0
---

# CarbonCalprac

Global-first carbon analysis workflow for study and prototyping.

Crafted by Abhirsc.

Current version: `0.3.0`

License: `Apache-2.0`

See [LICENSE](/Users/abhirsc/Documents/CarbonCalprac/LICENSE) for project licensing details.

The initial build target is a notebook-driven pipeline that:

1. Pulls open geospatial carbon-related datasets at global scale.
2. Clips them to an area of interest (AOI).
3. Prepares aligned analysis layers.
4. Computes summary indicators for emissions, sequestration, and storage.
5. Produces maps, tables, and simple report-ready insights.

Start with the implementation plan in `docs/global-carbon-pipeline.md` and the source inventory in `docs/open-data-inventory.md`.
