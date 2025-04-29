# render-bypass-apis/gunicorn_config.py
import os
import multiprocessing

# Render typically injects the PORT environment variable from its environment settings.
# Gunicorn will listen on all interfaces (0.0.0.0) on the port Render assigns.
# The default 10000 is a fallback if the PORT variable isn't set (unlikely on Render).
bind = "0.0.0.0:{}".format(os.environ.get("PORT", 10000))

# Adjust workers based on Render instance type.
# Render's WEB_CONCURRENCY environment variable is often a good starting point.
# Free/Starter tiers usually have limited CPU, so 2-3 workers is typical.
# You can experiment, but too many workers can hurt performance on small instances.
workers = int(os.environ.get("WEB_CONCURRENCY", 2))

# Increase the request timeout for potentially long scraping/bypass operations.
# Render itself might have higher-level timeouts, but this gives Gunicorn more time.
timeout = 120 # seconds (Increased from Gunicorn's default of 30)

# Optional: Logging configuration (uncomment and adjust if needed)
# Gunicorn logs to stdout/stderr by default, which Render captures.
# errorlog = '-'    # Log errors to stderr
# accesslog = '-'   # Log access requests to stdout
# loglevel = 'info' # Log level (debug, info, warning, error, critical)
