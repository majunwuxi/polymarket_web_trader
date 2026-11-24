import argparse
import logging
from pathlib import Path


def main() -> None:
    """启动 Dashboard API 服务"""
    parser = argparse.ArgumentParser(description="Polymarket Dashboard API")
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to config.yaml (default: config.yaml) - 已弃用，保留以兼容",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s"
    )
    
    logging.info("=" * 60)
    logging.info("Starting Polymarket Dashboard API")
    logging.info("=" * 60)
    
    # 导入并运行 dashboard_api
    import dashboard_api
    dashboard_api.init_services()
    port = dashboard_api.get_dashboard_port()
    dashboard_api.run_dashboard(port)


if __name__ == "__main__":
    main()
