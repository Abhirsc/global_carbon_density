# Open Data Inventory

## Selection rules

We want datasets that are:

- Global where possible.
- Open or low-friction access.
- Usable from Python.
- Reasonably documented.
- Good enough for study and prototyping.

## Recommended first-pass datasets

### 1. Sentinel-5P CH4 or CO

Role:

- Emission hotspot proxy.

Why use it:

- Global coverage.
- Good for atmospheric anomaly maps and AOI summaries.

Caveat:

- It is not a direct facility-level emission inventory.

### 2. MiCASA land carbon flux

Role:

- Sequestration or sink-side analysis.

Why use it:

- Global.
- Good for area-level carbon uptake trends.

Caveat:

- Modeled product, not a direct local field measurement.

### 3. GEDI biomass

Role:

- Storage proxy.

Why use it:

- Useful for aboveground biomass stock context.

Caveat:

- Coverage and sampling characteristics need to be checked for each AOI.

### 4. ESA WorldCover or equivalent land-cover layer

Role:

- Context layer for interpreting hotspots and sink areas.

Why use it:

- Helps explain where the signal occurs.

### 5. National or regional inventory overlays

Role:

- Validation and interpretation support.

Examples:

- Australia DCCEEW inventory datasets.
- NPI and NGER where relevant.

## Nice-to-have later

### Soil carbon

Possible source:

- SoilGrids or another global soil organic carbon source.

Use:

- Add belowground storage context.

### Fire emissions

Possible source:

- GFED or similar global fire emissions product.

Use:

- Separate wildfire-driven atmospheric signals from other sources.

## Data model to store for each dataset

Track these fields in code or metadata:

- Dataset name
- Provider
- Access method
- Native resolution
- Temporal frequency
- Units
- AOI support
- Citation
- Caveats

## Recommended v1 combination

Use these four first:

1. Sentinel-5P CH4
2. MiCASA land carbon flux
3. GEDI biomass
4. ESA WorldCover

That gives us one layer each for emission proxy, sequestration, storage, and land context.
