# Global Carbon Analysis Pipeline

## Goal

Build a study environment that works globally, then clips to an area of interest so the same workflow can be reused for Australia or any other region.

## Recommended first build

Start with a Jupyter notebook. It is the fastest way to validate data access, preprocessing, and metrics before we spend time on a UI.

After the notebook is stable, wrap the same functions in a small Streamlit app.

## Core design choice

Use a layered model instead of trying to infer everything from one satellite product.

- Emission signal layer: atmospheric concentration or hotspot proxy.
- Sequestration layer: land carbon uptake or vegetation productivity proxy.
- Storage layer: biomass or soil carbon stock proxy.
- Validation layer: inventory or model data used to ground the interpretation.

## Scope for v1

Focus on simple, defensible outputs:

1. AOI map with selected layers.
2. Time series for each layer.
3. Area summaries by land-cover class.
4. Basic anomaly view versus baseline period.
5. Exportable CSV summary.

Do not try to estimate exact project-level emissions from satellite imagery alone in v1.

## End-to-end workflow

### 1. Define the AOI

Inputs:

- GeoJSON polygon.
- Shapefile converted to GeoJSON.
- Latitude, longitude, and buffer radius.
- Country or state boundary from a public boundary dataset.

Output:

- A single AOI geometry in WGS84.

### 2. Choose time window

Inputs:

- Baseline period, for example `2019-01-01` to `2021-12-31`.
- Analysis period, for example `2022-01-01` to `2024-12-31`.

Output:

- Two comparable date ranges used consistently across layers.

### 3. Fetch raw source data

Minimum useful set:

- Emission proxy: Sentinel-5P methane or carbon monoxide.
- Sequestration proxy: MiCASA land carbon flux.
- Storage proxy: GEDI aboveground biomass or another biomass layer.
- Context layer: land cover.
- Optional validation layer: national or regional inventories.

Preferred access pattern:

1. Start with APIs or cloud-hosted raster assets.
2. If a dataset is easier through Earth Engine, use Earth Engine.
3. Keep every dataset behind a small fetch function so we can swap providers later.

### 4. Standardize raw layers

For every dataset:

- Convert timestamps to UTC and store a clear display timezone.
- Reproject to a common CRS only when needed for analysis.
- Record native resolution and units.
- Clip to AOI.
- Mask invalid pixels and missing data values.

Output:

- Clean AOI-clipped raster or table assets with metadata.

### 5. Harmonize spatial grids

Not all layers have the same resolution. For v1:

- Pick one analysis grid per workflow run.
- Resample coarse layers carefully.
- Do not oversell precision when combining mismatched products.

Recommended rule:

- Use the coarsest scientifically sensible grid among the selected layers for aggregated metrics.

### 6. Derive analysis-ready variables

Examples:

- Mean methane concentration over AOI by date.
- Biomass stock mean and total over AOI.
- Land carbon flux monthly mean by date.
- Area under high-anomaly threshold.
- Area by land-cover class intersecting a hotspot mask.

### 7. Compute insights

Recommended first insights:

1. Emission hotspot area:
   Area where the chosen emission proxy exceeds a baseline percentile or anomaly threshold.
2. Sequestration trend:
   Change in mean land carbon uptake over time.
3. Storage estimate:
   Total biomass stock within the AOI, with unit notes and caveats.
4. Land-cover context:
   Which land-cover classes explain most of the hotspot or sink area.
5. Confidence notes:
   State which outputs are direct measurements, modeled fields, or proxies.

### 8. Validate before interpretation

Checks:

- Missing months or temporal gaps.
- AOI too small for coarse rasters.
- Unit mismatches.
- Unexpected negative or extreme values.
- Visual comparison against inventory or known regional patterns.

### 9. Export outputs

Deliverables for each run:

- `summary_metrics.csv`
- `timeseries.csv`
- `aoi_layers.geojson`
- Map figures or notebook plots
- Short markdown report with caveats

## Suggested v1 dataset combination

### Global core

- Sentinel-5P CH4 or CO for hotspot proxy.
- MiCASA land carbon flux for sequestration.
- GEDI biomass for storage.
- ESA WorldCover or similar for land cover context.

This combination is realistic because it is global, fairly well documented, and can support AOI clipping.

## Suggested project layout

```text
CarbonCalprac/
  README.md
  docs/
    global-carbon-pipeline.md
    open-data-inventory.md
  data/
    raw/
    processed/
  notebooks/
    01_aoi_and_data_access.ipynb
    02_preprocessing_and_alignment.ipynb
    03_analysis_and_insights.ipynb
  src/
    carboncalprac/
      aoi.py
      fetch.py
      preprocess.py
      metrics.py
      report.py
```

## Build sequence

### Phase 1

- Lock the first dataset set.
- Create AOI utilities.
- Prove data fetch for one AOI and one date range.

### Phase 2

- Standardize and clip layers.
- Build first summary metrics.
- Plot maps and time series.

### Phase 3

- Add validation overlays.
- Add report export.
- Move shared logic into `src/`.

### Phase 4

- Wrap notebook logic in Streamlit.

## Practical note for Australia

The same global workflow can be clipped to Australia or a smaller Australian AOI. For reporting quality in Australia, we should later add:

- DCCEEW inventory context.
- NPI or NGER facility emissions where relevant.
- DEA land products if we want stronger Australian land-surface context.

## Immediate next implementation target

The first notebook should answer one narrow question well:

"For a chosen AOI and date range, what do methane hotspot proxy, land carbon flux, biomass stock, and land-cover context look like together?"

That gives us a concrete base before we expand into more advanced methods.
