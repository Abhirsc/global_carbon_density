from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import ee
import folium
from branca.element import Element
from folium.plugins import Draw


GAUL_LEVEL1 = "FAO/GAUL_SIMPLIFIED_500m/2015/level1"
BIOMASS_DATASET = "WCMC/biomass_carbon_density/v1_0"
S2_DATASET = "COPERNICUS/S2_SR_HARMONIZED"
DYNAMIC_WORLD_DATASET = "GOOGLE/DYNAMICWORLD/V1"

S2_BANDS = [
    "B1",
    "B2",
    "B3",
    "B4",
    "B5",
    "B6",
    "B7",
    "B8",
    "B8A",
    "B9",
    "B11",
    "B12",
]
PREDICTOR_BANDS = ["constant", *S2_BANDS, "NDVI"]
TREE_CLASS_VALUE = 1


@dataclass
class CarbonAnalysisConfig:
    start_date: str
    end_date: str
    output_dir: Path
    ee_project: str = "accessdata4app"
    admin0_name: str | None = None
    admin1_name: str | None = None
    aoi_geojson_path: Path | None = None
    map_name: str = "carbon_analysis_map.html"
    summary_name: str = "carbon_analysis_summary.json"
    export_prefix: str = "carbon_analysis"
    drive_folder: str = "CarbonCalprac"
    cloud_percentage_max: float = 10.0
    regression_scale_m: int = 250
    export_scale_m: int = 100
    start_exports: bool = False
    create_animation: bool = True


def _ensure_initialized(project: str) -> None:
    import json as _json
    import os as _os

    sa_key_json = _os.environ.get("EE_SERVICE_ACCOUNT_KEY")
    if sa_key_json:
        key_data = _json.loads(sa_key_json)
        credentials = ee.ServiceAccountCredentials(
            email=key_data["client_email"],
            key_data=sa_key_json,
        )
        ee.Initialize(credentials=credentials, project=project)
        return

    try:
        ee.Initialize(project=project)
    except Exception:
        ee.Authenticate()
        ee.Initialize(project=project)


def _load_geojson(path: Path) -> ee.Geometry:
    payload = json.loads(path.read_text())
    if payload.get("type") == "FeatureCollection":
        geometry = payload["features"][0]["geometry"]
    elif payload.get("type") == "Feature":
        geometry = payload["geometry"]
    else:
        geometry = payload
    return ee.Geometry(geometry)


def load_study_area(config: CarbonAnalysisConfig) -> tuple[ee.Geometry, str]:
    if config.aoi_geojson_path:
        geometry = _load_geojson(config.aoi_geojson_path)
        return geometry, config.aoi_geojson_path.stem

    if not config.admin0_name or not config.admin1_name:
        raise ValueError("Provide either --aoi-geojson or both --admin0-name and --admin1-name.")

    fc = ee.FeatureCollection(GAUL_LEVEL1)
    feature = (
        fc.filter(ee.Filter.eq("ADM0_NAME", config.admin0_name))
        .filter(ee.Filter.eq("ADM1_NAME", config.admin1_name))
        .first()
    )
    geometry = ee.Feature(feature).geometry()
    label = f"{config.admin0_name}_{config.admin1_name}".replace(" ", "_")
    return geometry, label


def list_admin1_names(admin0_name: str) -> list[str]:
    _ensure_initialized("accessdata4app")
    fc = ee.FeatureCollection(GAUL_LEVEL1).filter(ee.Filter.eq("ADM0_NAME", admin0_name))
    names = fc.aggregate_array("ADM1_NAME").getInfo() or []
    return sorted(set(names))


def _scale_s2(image: ee.Image) -> ee.Image:
    scaled = image.select(S2_BANDS).multiply(0.0001)
    ndvi = scaled.normalizedDifference(["B8", "B4"]).rename("NDVI")
    return scaled.addBands(ndvi).copyProperties(image, image.propertyNames())


def get_s2_predictors(study_area: ee.Geometry, config: CarbonAnalysisConfig) -> tuple[ee.Image, ee.Image]:
    collection = (
        ee.ImageCollection(S2_DATASET)
        .filterBounds(study_area)
        .filterDate(config.start_date, config.end_date)
        .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", config.cloud_percentage_max))
        .map(_scale_s2)
    )
    median = collection.median().clip(study_area)
    constant = ee.Image.constant(1).rename("constant").clip(study_area)
    predictor_image = constant.addBands(median.select(S2_BANDS + ["NDVI"]))
    return predictor_image, median


def get_dynamic_world_tree_mask(study_area: ee.Geometry, config: CarbonAnalysisConfig) -> tuple[ee.Image, ee.Image]:
    dw = (
        ee.ImageCollection(DYNAMIC_WORLD_DATASET)
        .filterBounds(study_area)
        .filterDate(config.start_date, config.end_date)
    )
    label_mode = dw.select("label").mode().clip(study_area)
    tree_mask = label_mode.eq(TREE_CLASS_VALUE).rename("tree_cover_mask")
    non_tree_mask = tree_mask.Not().rename("non_tree_mask")
    return tree_mask, non_tree_mask


def get_carbon_stock(study_area: ee.Geometry) -> tuple[ee.Image, list[str]]:
    biomass = ee.ImageCollection(BIOMASS_DATASET).first().clip(study_area)
    carbon_bands = biomass.bandNames().getInfo()
    carbon_stock = biomass.select([carbon_bands[0]]).rename("carbon_tonnes_per_ha")
    return carbon_stock, carbon_bands


def _percentile_vis(
    image: ee.Image,
    band: str,
    study_area: ee.Geometry,
    palette: list[str] | None = None,
    scale: int = 250,
    fallback_min: float = 0,
    fallback_max: float = 1,
) -> dict[str, Any]:
    try:
        stats = (
            image.select(band)
            .reduceRegion(
                reducer=ee.Reducer.percentile([5, 95]),
                geometry=study_area,
                scale=scale,
                bestEffort=True,
                maxPixels=1e13,
                tileScale=4,
            )
            .getInfo()
        )
    except Exception:
        stats = {}
    min_value = stats.get(f"{band}_p5", fallback_min)
    max_value = stats.get(f"{band}_p95", fallback_max)
    params: dict[str, Any] = {"bands": [band], "min": min_value, "max": max_value}
    if palette:
        params["palette"] = palette
    return params


def build_regression(
    predictors: ee.Image,
    carbon_stock: ee.Image,
    tree_mask: ee.Image,
    study_area: ee.Geometry,
    config: CarbonAnalysisConfig,
) -> dict[str, Any]:
    masked_predictors = predictors.updateMask(tree_mask)
    masked_carbon = carbon_stock.updateMask(tree_mask)
    regression_image = masked_predictors.addBands(masked_carbon)
    regression = regression_image.reduceRegion(
        reducer=ee.Reducer.robustLinearRegression(numX=len(PREDICTOR_BANDS), numY=1),
        geometry=study_area,
        scale=config.regression_scale_m,
        bestEffort=True,
        maxPixels=1e13,
    )

    coefficients_array = ee.Array(regression.get("coefficients"))
    coefficients_image = ee.Image.constant(coefficients_array.toList().flatten()).rename(PREDICTOR_BANDS)
    predicted = (
        masked_predictors.multiply(coefficients_image)
        .reduce(ee.Reducer.sum())
        .rename("predicted_carbon_tonnes_per_ha")
        .clip(study_area)
    )
    residuals = masked_carbon.subtract(predicted).rename("carbon_residual")
    rmse = ee.Number(
        residuals.pow(2)
        .reduceRegion(
            reducer=ee.Reducer.mean(),
            geometry=study_area,
            scale=config.regression_scale_m,
            bestEffort=True,
            maxPixels=1e13,
        )
        .get("carbon_residual")
    ).sqrt()

    coefficient_values = (
        coefficients_array.toList()
        .map(lambda row: ee.List(row).get(0))
        .getInfo()
    )

    return {
        "predictors": masked_predictors,
        "predicted": predicted,
        "residuals": residuals,
        "rmse": rmse.getInfo(),
        "coefficients": dict(zip(PREDICTOR_BANDS, coefficient_values)),
        "raw_regression": regression.getInfo(),
    }


def build_yearly_prediction_collection(
    study_area: ee.Geometry,
    tree_mask: ee.Image,
    coefficient_lookup: dict[str, float],
    config: CarbonAnalysisConfig,
) -> ee.ImageCollection:
    coefficient_image = ee.Image.constant([coefficient_lookup[band] for band in PREDICTOR_BANDS]).rename(PREDICTOR_BANDS)
    start_year = int(config.start_date[:4])
    end_year = int(config.end_date[:4])
    images: list[ee.Image] = []
    for year in range(start_year, end_year + 1):
        start = f"{year}-01-01"
        end = f"{year + 1}-01-01"
        year_config = CarbonAnalysisConfig(
            start_date=start,
            end_date=end,
            output_dir=config.output_dir,
            cloud_percentage_max=config.cloud_percentage_max,
        )
        predictors, _ = get_s2_predictors(study_area, year_config)
        predicted = (
            predictors.updateMask(tree_mask)
            .multiply(coefficient_image)
            .reduce(ee.Reducer.sum())
            .rename("predicted_carbon_tonnes_per_ha")
            .clip(study_area)
            .set("system:time_start", ee.Date(start).millis())
            .set("label", str(year))
        )
        images.append(predicted)
    return ee.ImageCollection(images)


def create_map(
    study_area: ee.Geometry,
    area_name: str,
    s2_median: ee.Image,
    tree_mask: ee.Image,
    non_tree_mask: ee.Image,
    carbon_stock: ee.Image,
    predicted: ee.Image,
    residuals: ee.Image,
) -> folium.Map:
    study_area_geojson = ee.FeatureCollection([ee.Feature(study_area, {"name": area_name})]).getInfo()
    center = study_area.centroid(1).coordinates().getInfo()
    m = folium.Map(location=[center[1], center[0]], zoom_start=8, control_scale=True)
    Draw(export=True).add_to(m)
    folium.GeoJson(study_area_geojson, name="Study Area").add_to(m)
    _add_ee_layer(
        m,
        s2_median,
        {"bands": ["B4", "B3", "B2"], "min": 0.02, "max": 0.3},
        "Sentinel-2 Median RGB",
    )
    _add_ee_layer(
        m,
        s2_median,
        {"bands": ["B8"], "min": 0.02, "max": 0.4, "palette": ["000004", "1f9e89", "fde725"]},
        "NIR Band (B8)",
    )
    _add_ee_layer(
        m,
        s2_median.select("NDVI"),
        {
            "bands": ["NDVI"],
            "min": -0.2,
            "max": 0.8,
            "palette": ["8c510a", "d8b365", "f6e8c3", "c7eae5", "5ab4ac", "01665e"],
        },
        "NDVI 90% Stretch",
    )
    _add_ee_layer(m, tree_mask.selfMask(), {"palette": ["006d2c"]}, "Dynamic World Tree Cover")
    _add_ee_layer(m, non_tree_mask.selfMask(), {"palette": ["bdbdbd"]}, "Dynamic World Non-Tree")
    _add_ee_layer(
        m,
        carbon_stock,
        {"bands": ["carbon_tonnes_per_ha"], "min": 0, "max": 150, "palette": ["f7fcf5", "74c476", "00441b"]},
        "Original Carbon Density",
    )
    _add_ee_layer(
        m,
        predicted,
        {
            "bands": ["predicted_carbon_tonnes_per_ha"],
            "min": 0,
            "max": 150,
            "palette": ["fff7bc", "fec44f", "d95f0e", "993404"],
        },
        "Predicted Carbon Density",
    )
    _add_ee_layer(
        m,
        residuals,
        {"bands": ["carbon_residual"], "min": -50, "max": 50, "palette": ["313695", "ffffbf", "a50026"]},
        "Residuals",
    )
    _add_compact_legend(m)
    folium.LayerControl().add_to(m)
    return m


def _add_ee_layer(map_object: folium.Map, image: ee.Image, vis_params: dict[str, Any], name: str) -> None:
    map_id = ee.Image(image).getMapId(vis_params)
    folium.raster_layers.TileLayer(
        tiles=map_id["tile_fetcher"].url_format,
        attr="Google Earth Engine",
        name=name,
        overlay=True,
        control=True,
    ).add_to(map_object)


def _add_compact_legend(map_object: folium.Map) -> None:
    legend_html = """
    <div style="
        position: fixed;
        bottom: 28px;
        left: 28px;
        z-index: 9999;
        background: rgba(255, 255, 255, 0.92);
        border: 1px solid #d8d8d8;
        border-radius: 8px;
        padding: 8px 10px;
        font-size: 11px;
        line-height: 1.25;
        min-width: 205px;
        box-shadow: 0 2px 8px rgba(0,0,0,0.12);
    ">
      <div style="font-weight: 700; margin-bottom: 6px;">Layer Legend</div>
      <div style="margin-bottom: 4px;"><span style="display:inline-block;width:14px;height:10px;background:linear-gradient(to right,#8c510a,#01665e);margin-right:6px;border:1px solid #bbb;"></span> NDVI: low to high vegetation</div>
      <div style="margin-bottom: 4px;"><span style="display:inline-block;width:14px;height:10px;background:#006d2c;margin-right:6px;border:1px solid #bbb;"></span> Tree cover mask</div>
      <div style="margin-bottom: 4px;"><span style="display:inline-block;width:14px;height:10px;background:linear-gradient(to right,#f7fcf5,#00441b);margin-right:6px;border:1px solid #bbb;"></span> Original carbon density</div>
      <div style="margin-bottom: 4px;"><span style="display:inline-block;width:14px;height:10px;background:linear-gradient(to right,#fff7bc,#993404);margin-right:6px;border:1px solid #bbb;"></span> Predicted carbon density</div>
      <div><span style="display:inline-block;width:14px;height:10px;background:linear-gradient(to right,#313695,#ffffbf,#a50026);margin-right:6px;border:1px solid #bbb;"></span> Residuals: negative to positive</div>
    </div>
    """
    map_object.get_root().html.add_child(Element(legend_html))


def start_exports(
    study_area: ee.Geometry,
    predicted: ee.Image,
    residuals: ee.Image,
    animation_collection: ee.ImageCollection | None,
    config: CarbonAnalysisConfig,
) -> dict[str, str]:
    tasks: dict[str, str] = {}
    export_region = study_area.bounds(1).coordinates().getInfo()
    predicted_task = ee.batch.Export.image.toDrive(
        image=predicted,
        description=f"{config.export_prefix}_estimated_carbon",
        folder=config.drive_folder,
        fileNamePrefix=f"{config.export_prefix}_estimated_carbon",
        region=export_region,
        scale=config.export_scale_m,
        crs="EPSG:4326",
        maxPixels=1e13,
    )
    predicted_task.start()
    tasks["estimated_carbon"] = predicted_task.status()["state"]

    difference_task = ee.batch.Export.image.toDrive(
        image=residuals,
        description=f"{config.export_prefix}_carbon_difference",
        folder=config.drive_folder,
        fileNamePrefix=f"{config.export_prefix}_carbon_difference",
        region=export_region,
        scale=config.export_scale_m,
        crs="EPSG:4326",
        maxPixels=1e13,
    )
    difference_task.start()
    tasks["difference"] = difference_task.status()["state"]

    if animation_collection is not None:
        styled = animation_collection.map(
            lambda image: image.visualize(
                min=0,
                max=150,
                palette=["fff7bc", "fec44f", "d95f0e", "993404"],
            )
        )
        video_task = ee.batch.Export.video.toDrive(
            collection=styled,
            description=f"{config.export_prefix}_carbon_animation",
            folder=config.drive_folder,
            fileNamePrefix=f"{config.export_prefix}_carbon_animation",
            dimensions=1080,
            framesPerSecond=1,
            region=export_region,
            crs="EPSG:4326",
            maxPixels=1e13,
        )
        video_task.start()
        tasks["animation_mp4"] = video_task.status()["state"]

    return tasks


def run_analysis(config: CarbonAnalysisConfig) -> dict[str, Any]:
    _ensure_initialized(config.ee_project)
    config.output_dir.mkdir(parents=True, exist_ok=True)
    study_area, area_name = load_study_area(config)
    predictors, s2_median = get_s2_predictors(study_area, config)
    tree_mask, non_tree_mask = get_dynamic_world_tree_mask(study_area, config)
    carbon_stock, carbon_source_bands = get_carbon_stock(study_area)
    regression = build_regression(predictors, carbon_stock, tree_mask, study_area, config)

    animation_collection = None
    if config.create_animation:
        animation_collection = build_yearly_prediction_collection(
            study_area,
            tree_mask,
            regression["coefficients"],
            config,
        )

    m = create_map(
        study_area,
        area_name,
        s2_median,
        tree_mask,
        non_tree_mask,
        carbon_stock,
        regression["predicted"],
        regression["residuals"],
    )
    map_path = config.output_dir / config.map_name
    m.save(str(map_path))

    export_status: dict[str, str] = {}
    if config.start_exports:
        export_status = start_exports(
            study_area,
            regression["predicted"],
            regression["residuals"],
            animation_collection,
            config,
        )

    summary = {
        "config": {
            key: (str(value) if isinstance(value, Path) else value)
            for key, value in asdict(config).items()
        },
        "study_area_name": area_name,
        "predictor_bands": PREDICTOR_BANDS,
        "carbon_source_bands": carbon_source_bands,
        "rmse_tonnes_per_ha": regression["rmse"],
        "coefficients": regression["coefficients"],
        "raw_regression": regression["raw_regression"],
        "map_path": str(map_path),
        "exports_started": export_status,
    }
    summary_path = config.output_dir / config.summary_name
    summary_path.write_text(json.dumps(summary, indent=2))

    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Earth Engine carbon analysis for a study area.")
    parser.add_argument("--start-date", help="Study start date, for example 2020-01-01.")
    parser.add_argument("--end-date", help="Study end date, for example 2021-12-31.")
    parser.add_argument("--ee-project", default="accessdata4app", help="Google Cloud project registered for Earth Engine.")
    parser.add_argument("--admin0-name", help="Country name in GAUL level-1.")
    parser.add_argument("--admin1-name", help="First-level administrative area name in GAUL level-1.")
    parser.add_argument("--aoi-geojson", type=Path, help="Path to a user-drawn polygon GeoJSON.")
    parser.add_argument("--output-dir", type=Path, default=Path("outputs/script_run"))
    parser.add_argument("--map-name", default="carbon_analysis_map.html")
    parser.add_argument("--summary-name", default="carbon_analysis_summary.json")
    parser.add_argument("--export-prefix", default="carbon_analysis")
    parser.add_argument("--drive-folder", default="CarbonCalprac")
    parser.add_argument("--cloud-percentage-max", type=float, default=10.0)
    parser.add_argument("--regression-scale-m", type=int, default=250)
    parser.add_argument("--export-scale-m", type=int, default=100)
    parser.add_argument("--start-exports", action="store_true")
    parser.add_argument("--skip-animation", action="store_true")
    parser.add_argument("--list-admin1", help="List all GAUL admin-1 names for a country and exit.")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.list_admin1:
        for name in list_admin1_names(args.list_admin1):
            print(name)
        return

    if not args.start_date or not args.end_date:
        parser.error("--start-date and --end-date are required unless --list-admin1 is used.")

    config = CarbonAnalysisConfig(
        ee_project=args.ee_project,
        start_date=args.start_date,
        end_date=args.end_date,
        output_dir=args.output_dir,
        admin0_name=args.admin0_name,
        admin1_name=args.admin1_name,
        aoi_geojson_path=args.aoi_geojson,
        map_name=args.map_name,
        summary_name=args.summary_name,
        export_prefix=args.export_prefix,
        drive_folder=args.drive_folder,
        cloud_percentage_max=args.cloud_percentage_max,
        regression_scale_m=args.regression_scale_m,
        export_scale_m=args.export_scale_m,
        start_exports=args.start_exports,
        create_animation=not args.skip_animation,
    )
    summary = run_analysis(config)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
