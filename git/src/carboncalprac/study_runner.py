from __future__ import annotations

import json
import logging
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import ee
import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
from PIL import Image, ImageDraw
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import Image as PdfImage
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .earth_engine_carbon import (
    CarbonAnalysisConfig,
    _ensure_initialized,
    build_regression,
    create_map,
    get_carbon_stock,
    get_dynamic_world_tree_mask,
    get_s2_predictors,
    load_study_area,
)
from .metadata import APP_AUTHOR, APP_CREDIT_LINE, APP_LICENSE_NAME, APP_LICENSE_NOTE, APP_NAME, APP_TITLE, APP_VERSION


ProgressCallback = Callable[[float, str], None]


def setup_run_logger(output_dir: Path) -> tuple[logging.Logger, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    log_path = output_dir / "run.log"
    logger_name = f"carboncalprac.{output_dir.as_posix()}"
    logger = logging.getLogger(logger_name)
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    handler = logging.FileHandler(log_path)
    handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
    logger.addHandler(handler)
    logger.propagate = False
    logger.info("Run logger started.")
    return logger, log_path


def emit_progress(progress_callback: ProgressCallback | None, logger: logging.Logger | None, value: float, message: str) -> None:
    if logger is not None:
        logger.info(message)
    if progress_callback is not None:
        progress_callback(value, message)


def list_admin0_names(project: str = "accessdata4app") -> list[str]:
    _ensure_initialized(project)
    names = ee.FeatureCollection("FAO/GAUL_SIMPLIFIED_500m/2015/level1").aggregate_array("ADM0_NAME").getInfo() or []
    return sorted(set(names))


def write_uploaded_boundary(upload_bytes: bytes, original_name: str, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(original_name).suffix.lower()
    target_path = output_dir / f"uploaded_boundary{suffix or '.geojson'}"
    target_path.write_bytes(upload_bytes)

    if suffix in {".geojson", ".json"}:
        return target_path

    gdf = gpd.read_file(target_path)
    geojson_path = output_dir / "uploaded_boundary.geojson"
    geojson_path.write_text(gdf.to_json())
    return geojson_path


def write_drawn_boundary(feature_collection: dict[str, Any], output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    geojson_path = output_dir / "drawn_boundary.geojson"
    geojson_path.write_text(json.dumps(feature_collection, indent=2))
    return geojson_path


def choose_preview_scale(area_m2: float) -> int:
    area_km2 = area_m2 / 1_000_000
    if area_km2 <= 150:
        return 30
    if area_km2 <= 1_000:
        return 60
    if area_km2 <= 5_000:
        return 120
    return 250


def save_ee_thumbnail(
    image: ee.Image,
    vis_params: dict[str, Any],
    study_area: ee.Geometry,
    output_path: Path,
    render_scale_m: int,
) -> Path:
    rectangle = (
        ee.Image(image)
        .visualize(**vis_params)
        .select(["vis-red", "vis-green", "vis-blue"])
        .reproject(crs="EPSG:4326", scale=render_scale_m)
        .sampleRectangle(region=study_area.bounds(1), defaultValue=0)
        .getInfo()
    )
    properties = rectangle.get("properties", rectangle)
    rgb = np.stack(
        [
            np.array(properties["vis-red"], dtype=float),
            np.array(properties["vis-green"], dtype=float),
            np.array(properties["vis-blue"], dtype=float),
        ],
        axis=-1,
    )
    rgb = np.clip(rgb / 255.0, 0, 1)
    plt.imsave(output_path, rgb)
    return output_path


def create_layer_pngs(
    study_area: ee.Geometry,
    s2_median: ee.Image,
    tree_mask: ee.Image,
    carbon_stock: ee.Image,
    predicted: ee.Image,
    residuals: ee.Image,
    output_dir: Path,
    logger: logging.Logger | None = None,
) -> dict[str, str]:
    images_dir = output_dir / "report_assets"
    images_dir.mkdir(parents=True, exist_ok=True)
    area_m2 = float(study_area.area(1).getInfo())
    preview_scale_m = choose_preview_scale(area_m2)
    if logger is not None:
        logger.info("Saving report PNG layers with adaptive preview scale %sm.", preview_scale_m)
    return {
        "rgb_png": str(
            save_ee_thumbnail(
                s2_median,
                {"bands": ["B4", "B3", "B2"], "min": 0.02, "max": 0.3},
                study_area,
                images_dir / "sentinel_rgb.png",
                render_scale_m=preview_scale_m,
            )
        ),
        "ndvi_png": str(
            save_ee_thumbnail(
                s2_median.select("NDVI"),
                {
                    "bands": ["NDVI"],
                    "min": -0.2,
                    "max": 0.8,
                    "palette": ["8c510a", "d8b365", "f6e8c3", "c7eae5", "5ab4ac", "01665e"],
                },
                study_area,
                images_dir / "ndvi.png",
                render_scale_m=preview_scale_m,
            )
        ),
        "tree_mask_png": str(
            save_ee_thumbnail(
                tree_mask.selfMask(),
                {"palette": ["006d2c"]},
                study_area,
                images_dir / "tree_mask.png",
                render_scale_m=preview_scale_m,
            )
        ),
        "original_carbon_png": str(
            save_ee_thumbnail(
                carbon_stock,
                {"bands": ["carbon_tonnes_per_ha"], "min": 0, "max": 150, "palette": ["f7fcf5", "74c476", "00441b"]},
                study_area,
                images_dir / "original_carbon.png",
                render_scale_m=preview_scale_m,
            )
        ),
        "predicted_carbon_png": str(
            save_ee_thumbnail(
                predicted,
                {
                    "bands": ["predicted_carbon_tonnes_per_ha"],
                    "min": 0,
                    "max": 150,
                    "palette": ["fff7bc", "fec44f", "d95f0e", "993404"],
                },
                study_area,
                images_dir / "predicted_carbon.png",
                render_scale_m=preview_scale_m,
            )
        ),
        "residuals_png": str(
            save_ee_thumbnail(
                residuals,
                {"bands": ["carbon_residual"], "min": -50, "max": 50, "palette": ["313695", "ffffbf", "a50026"]},
                study_area,
                images_dir / "residuals.png",
                render_scale_m=preview_scale_m,
            )
        ),
        "preview_scale_m": str(preview_scale_m),
    }


def create_comparison_gif(report_assets: dict[str, str], output_dir: Path) -> str:
    original = Image.open(report_assets["original_carbon_png"]).convert("RGBA")
    predicted = Image.open(report_assets["predicted_carbon_png"]).convert("RGBA")
    frames: list[Image.Image] = []

    for label, base_image in [("Original carbon density", original), ("Predicted carbon density", predicted)]:
        frame = base_image.copy()
        draw = ImageDraw.Draw(frame)
        draw.rectangle((20, 20, 360, 70), fill=(255, 255, 255, 220))
        draw.text((30, 35), label, fill=(0, 0, 0, 255))
        frames.append(frame.convert("P", palette=Image.ADAPTIVE))

    gif_path = output_dir / "carbon_original_vs_predicted.gif"
    frames[0].save(gif_path, save_all=True, append_images=frames[1:], duration=1400, loop=0)
    return str(gif_path)


def load_run_history(history_path: Path) -> list[dict[str, Any]]:
    if not history_path.exists():
        return []
    return json.loads(history_path.read_text())


def update_run_history(base_dir: Path, summary: dict[str, Any]) -> dict[str, Any]:
    history_path = base_dir / "run_history.json"
    history = load_run_history(history_path)
    history.append(
        {
            "generated_at": summary["generated_at"],
            "study_area_name": summary["study_area_name"],
            "start_date": summary["config"]["start_date"],
            "end_date": summary["config"]["end_date"],
            "rmse_tonnes_per_ha": summary["rmse_tonnes_per_ha"],
            "output_dir": summary["output_dir"],
        }
    )
    history_path.parent.mkdir(parents=True, exist_ok=True)
    history_path.write_text(json.dumps(history[-200:], indent=2))

    today = datetime.now().date().isoformat()
    todays_runs = [row for row in history if row["generated_at"][:10] == today]
    return {
        "history_path": str(history_path),
        "todays_local_runs": len(todays_runs),
        "memory_quota_estimate": "Unknown",
        "memory_quota_note": (
            "Earth Engine does not expose an exact 'runs left' counter for memory-limited requests. "
            "'User memory limit exceeded' is a per-request constraint, so remaining runs depend on AOI size, layer complexity, and current server-side workload."
        ),
    }


def build_pdf_report(summary: dict[str, Any], pdf_path: Path) -> Path:
    styles = getSampleStyleSheet()
    doc = SimpleDocTemplate(str(pdf_path), pagesize=A4, rightMargin=1.5 * cm, leftMargin=1.5 * cm)
    story: list[Any] = []

    story.append(Paragraph(f"{APP_TITLE}", styles["Title"]))
    story.append(Spacer(1, 0.3 * cm))
    story.append(Paragraph(f"{APP_CREDIT_LINE} | Version {APP_VERSION} | License: {APP_LICENSE_NAME}", styles["BodyText"]))
    story.append(Paragraph(APP_LICENSE_NOTE, styles["BodyText"]))
    story.append(Spacer(1, 0.2 * cm))
    story.append(Paragraph(f"Study area: {summary['study_area_name']}", styles["Heading2"]))
    story.append(
        Paragraph(
            f"Study period: {summary['config']['start_date']} to {summary['config']['end_date']} | "
            f"Analysis resolution: {summary['config']['regression_scale_m']} m",
            styles["BodyText"],
        )
    )
    story.append(Spacer(1, 0.3 * cm))
    story.append(Paragraph("What happened", styles["Heading2"]))
    for text in [
        "1. The user selected or supplied a study area boundary.",
        "2. Sentinel-2 scenes were filtered to the selected date range and cloud threshold, scaled, and composited.",
        "3. Dynamic World tree cover was used to focus the regression on forested areas.",
        "4. Biomass carbon density was clipped to the study area.",
        "5. A robust linear regression was fit at 250 m to estimate carbon stock from the predictor stack.",
        "6. Predicted carbon, residuals, and a study map were generated for learning and interpretation.",
    ]:
        story.append(Paragraph(text, styles["BodyText"]))
    story.append(Spacer(1, 0.3 * cm))

    story.append(Paragraph("Calculation summary", styles["Heading2"]))
    summary_table = Table(
        [
            ["Metric", "Value"],
            ["RMSE (tonnes per hectare)", f"{summary['rmse_tonnes_per_ha']:.4f}"],
            ["Output directory", summary["output_dir"]],
            ["Map HTML", summary["map_path"]],
            ["Reproducibility log", summary["log_path"]],
            ["Comparison GIF", summary.get("comparison_gif_path", "Not generated")],
            ["Preview render scale", f"{summary['preview_scale_m']} m"],
            ["Version", APP_VERSION],
            ["Crafted by", APP_AUTHOR],
            ["License", APP_LICENSE_NAME],
        ],
        colWidths=[6 * cm, 10 * cm],
    )
    summary_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#dceaf8")),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    story.append(summary_table)
    story.append(Spacer(1, 0.4 * cm))

    story.append(Paragraph("Layer snapshots", styles["Heading2"]))
    for label, key in [
        ("Sentinel-2 RGB", "rgb_png"),
        ("NDVI", "ndvi_png"),
        ("Tree cover mask", "tree_mask_png"),
        ("Original carbon density", "original_carbon_png"),
        ("Predicted carbon density", "predicted_carbon_png"),
        ("Residuals", "residuals_png"),
    ]:
        image_path = summary["report_assets"].get(key)
        if not image_path:
            continue
        story.append(Paragraph(label, styles["Heading3"]))
        story.append(PdfImage(image_path, width=16 * cm, height=9 * cm))
        story.append(Spacer(1, 0.2 * cm))

    if summary.get("comparison_gif_path"):
        story.append(Paragraph("Animation note", styles["Heading2"]))
        story.append(
            Paragraph(
                "A downloadable GIF comparing original and predicted carbon density was generated for this run. "
                "The PDF references it, while the animated file is kept as a separate download artifact.",
                styles["BodyText"],
            )
        )

    story.append(Paragraph("Regression coefficients", styles["Heading2"]))
    coeff_rows = [["Band", "Coefficient"]]
    coeff_rows.extend([[band, f"{value:.6f}"] for band, value in summary["coefficients"].items()])
    coeff_table = Table(coeff_rows, colWidths=[5 * cm, 5 * cm])
    coeff_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f4f4f4")),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
            ]
        )
    )
    story.append(coeff_table)
    doc.build(story)
    return pdf_path


def run_study_with_artifacts(
    config: CarbonAnalysisConfig,
    progress_callback: ProgressCallback | None = None,
) -> dict[str, Any]:
    logger, log_path = setup_run_logger(config.output_dir)
    emit_progress(progress_callback, logger, 0.05, "Initializing Earth Engine.")
    _ensure_initialized(config.ee_project)
    config.output_dir.mkdir(parents=True, exist_ok=True)

    emit_progress(progress_callback, logger, 0.15, "Loading the study area.")
    study_area, area_name = load_study_area(config)

    emit_progress(progress_callback, logger, 0.3, "Preparing Sentinel-2 predictors and NDVI.")
    predictors, s2_median = get_s2_predictors(study_area, config)

    emit_progress(progress_callback, logger, 0.45, "Building the Dynamic World tree mask.")
    tree_mask, non_tree_mask = get_dynamic_world_tree_mask(study_area, config)

    emit_progress(progress_callback, logger, 0.55, "Clipping the carbon density layer.")
    carbon_stock, carbon_source_bands = get_carbon_stock(study_area)

    emit_progress(progress_callback, logger, 0.7, "Running the 250 m robust regression model.")
    regression = build_regression(predictors, carbon_stock, tree_mask, study_area, config)

    emit_progress(progress_callback, logger, 0.82, "Building the interactive map.")
    result_map = create_map(
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
    result_map.save(str(map_path))

    emit_progress(progress_callback, logger, 0.9, "Saving report PNG layers.")
    report_assets = create_layer_pngs(
        study_area,
        s2_median,
        tree_mask,
        carbon_stock,
        regression["predicted"],
        regression["residuals"],
        config.output_dir,
        logger=logger,
    )
    comparison_gif_path = create_comparison_gif(report_assets, config.output_dir)

    summary = {
        "config": {
            key: (str(value) if isinstance(value, Path) else value)
            for key, value in asdict(config).items()
        },
        "project_metadata": {
            "name": APP_NAME,
            "version": APP_VERSION,
            "crafted_by": APP_AUTHOR,
            "license": APP_LICENSE_NAME,
            "license_note": APP_LICENSE_NOTE,
        },
        "study_area_name": area_name,
        "predictor_bands": list(regression["coefficients"].keys()),
        "carbon_source_bands": carbon_source_bands,
        "rmse_tonnes_per_ha": regression["rmse"],
        "coefficients": regression["coefficients"],
        "raw_regression": regression["raw_regression"],
        "map_path": str(map_path),
        "output_dir": str(config.output_dir),
        "log_path": str(log_path),
        "report_assets": report_assets,
        "preview_scale_m": int(report_assets["preview_scale_m"]),
        "comparison_gif_path": comparison_gif_path,
        "comparison_mp4_path": None,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }

    quota_info = update_run_history(config.output_dir.parent, summary)
    summary["quota_status"] = quota_info

    summary_path = config.output_dir / config.summary_name
    summary_path.write_text(json.dumps(summary, indent=2))

    emit_progress(progress_callback, logger, 0.96, "Writing the PDF report.")
    pdf_path = build_pdf_report(summary, config.output_dir / "carbon_analysis_report.pdf")
    summary["pdf_report_path"] = str(pdf_path)
    summary_path.write_text(json.dumps(summary, indent=2))

    emit_progress(progress_callback, logger, 1.0, "Calculation completed.")
    logger.info("Run completed successfully.")
    logger.info("Metadata | version=%s | crafted_by=%s | license=%s", APP_VERSION, APP_AUTHOR, APP_LICENSE_NAME)
    return summary


def create_run_directory(base_dir: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return base_dir / stamp
