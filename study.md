# CarbonCalprac Study Guide

Updated: 2026-03-26

Crafted by Abhirsc

Version: `0.3.0`

License: `Apache-2.0`

## What this version is

This project version starts with a functional Python script before converting the workflow into notebooks.

The goal is to study how Earth Engine can be used to:

- define a study area
- clip carbon-related layers to that study area only
- derive vegetation predictors from Sentinel-2
- isolate forested areas with Dynamic World
- fit a regression model between predictor layers and carbon density
- estimate carbon stock for the study area
- export map-ready outputs

## Learning style for this project

Use this order when learning and running the project:

1. Read the theory and data roles first.
2. Understand the study-area input options.
3. Understand what each dataset contributes.
4. Run the script for one small study area.
5. Inspect the saved HTML map and JSON summary.
6. Only after the script works, move the same steps into notebooks.

This is the best path because the script keeps the logic in one place, which makes debugging easier.

## Core theory behind the workflow

### 1. Study area

Every raster operation depends on a single geometry called the study area.

In this project the study area can come from:

- a first-level GAUL administrative boundary
- a user polygon stored in GeoJSON

Why this matters:

- clipping early reduces data volume
- statistics only use the place we care about
- exports stay focused on the chosen region

### 2. Carbon density layer

The WCMC biomass carbon density dataset gives aboveground and belowground terrestrial carbon storage in tonnes of carbon per hectare.

In this version, one carbon density band is used as the response variable for regression.

Interpretation:

- this is a stock layer, not a yearly emission layer
- it represents stored carbon rather than flux

### 3. Sentinel-2 predictors

Sentinel-2 SR Harmonized is used to create reflectance-based predictors.

Key processing steps:

- filter by study area
- filter by time period
- keep scenes with cloud percentage less than 10
- select all main spectral bands
- multiply by `0.0001` scale factor
- build a median composite
- calculate NDVI using `B8` and `B4`

Why NDVI:

- NDVI is a vegetation vigor proxy
- greener, denser vegetation often correlates with higher biomass and carbon storage

### 4. Dynamic World tree cover

Dynamic World is used to isolate tree-covered land.

Key processing steps:

- filter to the same time period
- select the `label` band
- apply `mode()` across the period
- use `.eq(1)` to keep tree pixels

Why this matters:

- it removes many non-forest pixels
- the regression is focused on places where biomass-carbon relationships are more meaningful

### 5. Regression model

The current script uses a robust linear regression in Earth Engine.

Predictors:

- constant band
- Sentinel-2 spectral bands
- NDVI

Response:

- carbon tonnes per hectare

Why robust regression:

- it is less sensitive to outliers than ordinary least squares
- remote-sensing datasets can contain noisy or mixed pixels

### 6. Prediction and residuals

Once coefficients are estimated:

- predictor layers are multiplied by the coefficients
- bands are summed
- the result is a predicted carbon stock surface

Then the script calculates:

- residuals = original carbon density minus predicted carbon density
- RMSE = root mean square error over the study area at 250 m model scale

This tells us how close the prediction is to the source carbon-density surface.

### 7. Export idea

The current script supports Earth Engine export tasks for:

- estimated carbon stock raster
- difference or residual raster
- optional MP4 animation of annual predicted carbon surfaces

## How to use this script version

### Option 1. Use an admin boundary

Example:

```bash
PYTHONPATH=src python3 scripts/run_carbon_analysis.py \
  --start-date 2020-01-01 \
  --end-date 2021-12-31 \
  --admin0-name Australia \
  --admin1-name Queensland
```

### Option 2. Use a drawn polygon

1. Draw or create a polygon and save it as GeoJSON.
2. Run:

```bash
PYTHONPATH=src python3 scripts/run_carbon_analysis.py \
  --start-date 2020-01-01 \
  --end-date 2021-12-31 \
  --aoi-geojson path/to/study_area.geojson
```

### Optional helper

To discover valid first-level admin names:

```bash
PYTHONPATH=src python3 scripts/run_carbon_analysis.py --list-admin1 Australia
```

## Expected outputs

The script writes to `outputs/script_run/` by default:

- an HTML leaflet-style map
- a JSON summary with coefficients and RMSE

If exports are started, Earth Engine creates Drive tasks for:

- estimated carbon output
- carbon difference output
- animation MP4

## Important limitations in this first script build

- The biomass carbon dataset is centered on around 2010, while Sentinel-2 and Dynamic World are 2020+ products.
- The model is therefore a study workflow, not a true same-date validation workflow.
- The “draw polygon” experience is not yet a full standalone app; for now the script accepts a GeoJSON polygon as input.
- Exported animation is based on yearly predicted surfaces, which is a first-pass visualization rather than a formal change-detection product.

## What to learn next

After this script works, the next study steps are:

1. Split the code into AOI, fetch, model, and export modules.
2. Convert the workflow into step-by-step notebooks.
3. Add stronger cloud masking for Sentinel-2.
4. Inspect the exact WCMC band choice more carefully for aboveground and belowground summaries.
5. Add proper validation tables and plots.

## Local study app flow

The project now also has a local app flow for learning.

What the app does:

- lets the user choose a first-level admin boundary
- lets the user draw a polygon
- lets the user upload a boundary file
- lets the user choose from and to dates
- starts the calculation only after AOI and time window are selected
- shows a progress bar while the workflow is running
- keeps the current analysis workflow at 250 m
- shows the final map and calculation summary
- creates a PDF report with learning-oriented explanation and layer images
- writes a run log for reproducibility

Why this matters for study:

- the user can see the workflow as a guided sequence instead of a hidden script
- the progress bar makes long Earth Engine steps understandable
- the report and log help explain not only the result, but the process
