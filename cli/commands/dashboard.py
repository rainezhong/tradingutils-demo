"""Dashboard command for the trading CLI.

Launches the web dashboard for visualizing algorithm activity.
"""

import click


@click.command()
@click.option("--host", default="127.0.0.1", help="Host to bind to")
@click.option("--port", default=8080, help="Port to bind to")
@click.option("--reload", is_flag=True, help="Enable auto-reload for development")
def dashboard(host, port, reload):
    """Launch the web dashboard for algorithm monitoring."""
    try:
        import uvicorn
        from dashboard.app import create_app

        click.echo(f"Starting dashboard at http://{host}:{port}")
        click.echo("Press Ctrl+C to stop")

        app = create_app()
        uvicorn.run(
            app,
            host=host,
            port=port,
            reload=reload,
            log_level="info"
        )
    except ImportError as e:
        click.echo(f"Error: Missing dependency - {e}")
        click.echo("Install with: pip install uvicorn fastapi")
        raise SystemExit(1)
    except Exception as e:
        click.echo(f"Error starting dashboard: {e}")
        raise SystemExit(1)
