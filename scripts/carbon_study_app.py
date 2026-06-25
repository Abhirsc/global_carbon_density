from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components
from streamlit_folium import st_folium

import folium
from folium.plugins import Draw

from carboncalprac.earth_engine_carbon import CarbonAnalysisConfig, list_admin1_names
from carboncalprac.metadata import APP_AUTHOR, APP_CREDIT_LINE, APP_LICENSE_NAME, APP_TITLE, APP_VERSION
from carboncalprac.study_runner import (
    create_run_directory,
    list_admin0_names,
    load_run_history,
    run_study_with_artifacts,
    write_drawn_boundary,
    write_uploaded_boundary,
)


BASE_OUTPUT_DIR = Path("outputs/app_runs")
DEFAULT_PROJECT = "accessdata4app"


def make_input_map() -> folium.Map:
    m = folium.Map(location=[-25.0, 133.0], zoom_start=4, control_scale=True)
    Draw(export=False).add_to(m)
    return m


def main() -> None:
    st.set_page_config(page_title=f"{APP_TITLE} v{APP_VERSION}", layout="wide")
    st.title(APP_TITLE)
    st.caption(f"Select a study area, choose a time window, and run a 250 m carbon-learning workflow. {APP_CREDIT_LINE}.")

    if "drawn_geojson" not in st.session_state:
        st.session_state["drawn_geojson"] = None

    with st.sidebar:
        st.subheader("Study Setup")
        st.caption(f"Version {APP_VERSION} | {APP_CREDIT_LINE} | License: {APP_LICENSE_NAME}")
        ee_project = st.text_input("Earth Engine project", value=DEFAULT_PROJECT)
        aoi_mode = st.radio(
            "Area of interest input",
            ["First-level boundary selector", "Draw a polygon", "Upload a boundary file"],
        )

        admin0_name = None
        admin1_name = None
        uploaded_geojson_bytes = None
        uploaded_name = None

        if aoi_mode == "First-level boundary selector":
            admin0_options = list_admin0_names(ee_project)
            admin0_name = st.selectbox("Country", admin0_options, index=admin0_options.index("Australia") if "Australia" in admin0_options else 0)
            admin1_options = list_admin1_names(admin0_name)
            admin1_name = st.selectbox("First-level boundary", admin1_options)

        if aoi_mode == "Upload a boundary file":
            uploaded_file = st.file_uploader("Upload GeoJSON, JSON, ZIP shapefile, or GeoPackage", type=["geojson", "json", "zip", "gpkg"])
            if uploaded_file is not None:
                uploaded_geojson_bytes = uploaded_file.getvalue()
                uploaded_name = uploaded_file.name

        start_date = st.date_input("From date", value=date(2020, 1, 1))
        end_date = st.date_input("To date", value=date(2021, 12, 31))
        st.caption("Current analysis grid is fixed at 250 m for consistency.")
        run_button = st.button("Start calculation", type="primary", use_container_width=True)

        history_path = BASE_OUTPUT_DIR / "run_history.json"
        history = load_run_history(history_path)
        todays_runs = [row for row in history if row["generated_at"][:10] == date.today().isoformat()]
        st.subheader("Quota status")
        st.metric("Today's local runs", len(todays_runs))
        st.info(
            "Exact Earth Engine memory runs left cannot be read from the API. "
            "'User memory limit exceeded' depends on AOI size and request complexity, not just a daily counter."
        )

    if aoi_mode == "Draw a polygon":
        st.subheader("Draw your study area")
        st.write("Draw a polygon on the map below. The last drawn polygon will be used for the calculation.")
        draw_result = st_folium(make_input_map(), height=500, width=None, returned_objects=["all_drawings", "last_active_drawing"])
        drawn_feature = draw_result.get("last_active_drawing")
        all_drawings = draw_result.get("all_drawings") or []
        if drawn_feature:
            st.session_state["drawn_geojson"] = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {}, "geometry": drawn_feature["geometry"]}]}
            st.success("A drawn polygon is ready to use.")
        elif all_drawings:
            last_geometry = all_drawings[-1]["geometry"]
            st.session_state["drawn_geojson"] = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {}, "geometry": last_geometry}]}
            st.success("A drawn polygon is ready to use.")
        elif st.session_state["drawn_geojson"] is not None:
            st.info("Using the last polygon you drew in this session.")
        else:
            st.warning("Draw a polygon before starting the calculation.")

    if run_button:
        run_dir = create_run_directory(BASE_OUTPUT_DIR)
        geojson_path = None

        if aoi_mode == "Draw a polygon":
            if st.session_state["drawn_geojson"] is None:
                st.error("Draw a polygon first.")
                st.stop()
            geojson_path = write_drawn_boundary(st.session_state["drawn_geojson"], run_dir)

        if aoi_mode == "Upload a boundary file":
            if uploaded_geojson_bytes is None or uploaded_name is None:
                st.error("Upload a boundary file first.")
                st.stop()
            geojson_path = write_uploaded_boundary(uploaded_geojson_bytes, uploaded_name, run_dir)

        config = CarbonAnalysisConfig(
            ee_project=ee_project,
            start_date=start_date.isoformat(),
            end_date=end_date.isoformat(),
            output_dir=run_dir,
            admin0_name=admin0_name,
            admin1_name=admin1_name,
            aoi_geojson_path=geojson_path,
            regression_scale_m=250,
            export_scale_m=250,
            create_animation=False,
        )

        progress_bar = st.progress(0)
        status_box = st.empty()

        def update_progress(value: float, message: str) -> None:
            progress_bar.progress(min(max(int(value * 100), 0), 100))
            status_box.info(message)

        summary = run_study_with_artifacts(config, progress_callback=update_progress)
        progress_bar.progress(100)
        status_box.success("Calculation completed.")

        st.subheader("Results")
        col1, col2, col3 = st.columns(3)
        col1.metric("RMSE (t/ha)", f"{summary['rmse_tonnes_per_ha']:.2f}")
        col2.metric("Analysis resolution", f"{summary['config']['regression_scale_m']} m")
        col3.metric("Study area", summary["study_area_name"])
        st.caption(
            f"Analysis stays at {summary['config']['regression_scale_m']} m. "
            f"Preview PNGs use an adaptive render scale of {summary['preview_scale_m']} m for clearer small-area snapshots."
        )

        st.write("Final calculation summary")
        st.json(
            {
                "version": APP_VERSION,
                "crafted_by": APP_AUTHOR,
                "license": APP_LICENSE_NAME,
                "study_area_name": summary["study_area_name"],
                "start_date": summary["config"]["start_date"],
                "end_date": summary["config"]["end_date"],
                "rmse_tonnes_per_ha": summary["rmse_tonnes_per_ha"],
                "preview_scale_m": summary["preview_scale_m"],
                "todays_local_runs": summary["quota_status"]["todays_local_runs"],
                "memory_quota_estimate": summary["quota_status"]["memory_quota_estimate"],
                "coefficients": summary["coefficients"],
            }
        )

        st.subheader("Quota and run status")
        quota_col1, quota_col2 = st.columns(2)
        quota_col1.metric("Today's local runs", summary["quota_status"]["todays_local_runs"])
        quota_col2.metric("Estimated runs left", summary["quota_status"]["memory_quota_estimate"])
        st.caption(summary["quota_status"]["memory_quota_note"])

        st.subheader("Interactive map")
        components.html(Path(summary["map_path"]).read_text(), height=700, scrolling=True)
        st.caption("A compact legend is shown on the map. A quick reference legend is also listed below.")

        st.subheader("Layer legend")
        legend_col1, legend_col2 = st.columns(2)
        legend_col1.markdown(
            """
            - `Sentinel-2 RGB`: natural-color median composite
            - `NIR Band (B8)`: stronger vegetation reflectance appears brighter
            - `NDVI`: brown to teal-green means low to high vegetation vigor
            """
        )
        legend_col2.markdown(
            """
            - `Tree Cover`: dark green forest mask from Dynamic World
            - `Original Carbon`: pale to dark green means lower to higher source carbon density
            - `Predicted Carbon`: pale yellow to brown means lower to higher modeled carbon
            - `Residuals`: blue to red means underprediction to overprediction
            """
        )

        st.subheader("Layer snapshots for learning")
        image_cols = st.columns(3)
        image_items = [
            ("Sentinel-2 RGB", summary["report_assets"]["rgb_png"]),
            ("NDVI", summary["report_assets"]["ndvi_png"]),
            ("Tree mask", summary["report_assets"]["tree_mask_png"]),
            ("Original carbon", summary["report_assets"]["original_carbon_png"]),
            ("Predicted carbon", summary["report_assets"]["predicted_carbon_png"]),
            ("Residuals", summary["report_assets"]["residuals_png"]),
        ]
        for idx, (label, path) in enumerate(image_items):
            image_cols[idx % 3].image(path, caption=label, use_container_width=True)

        st.subheader("Downloads")
        with open(summary["pdf_report_path"], "rb") as pdf_file:
            st.download_button("Download PDF report", data=pdf_file.read(), file_name=Path(summary["pdf_report_path"]).name, mime="application/pdf")
        with open(summary["log_path"], "rb") as log_file:
            st.download_button("Download run log", data=log_file.read(), file_name=Path(summary["log_path"]).name, mime="text/plain")
        with open(summary["map_path"], "rb") as map_file:
            st.download_button("Download map HTML", data=map_file.read(), file_name=Path(summary["map_path"]).name, mime="text/html")
        with open(summary["comparison_gif_path"], "rb") as gif_file:
            st.download_button("Download comparison GIF", data=gif_file.read(), file_name=Path(summary["comparison_gif_path"]).name, mime="image/gif")

        st.subheader("Animation")
        st.image(summary["comparison_gif_path"], caption="Original vs predicted carbon density")
        st.caption("GIF is available now. MP4 is not generated yet in the app flow.")

        st.subheader("Reproducibility")
        st.code(Path(summary["log_path"]).read_text(), language="text")

    st.divider()
    st.caption(f"{APP_CREDIT_LINE} | Version {APP_VERSION} | License: {APP_LICENSE_NAME}")


if __name__ == "__main__":
    main()
