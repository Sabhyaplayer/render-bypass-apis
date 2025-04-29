# render-bypass-apis/gunicorn_config.py
import multiprocessing

# Render typically injects the PORT environment variable
bind = "0.0.0.0:{}".format(os.environ.get("PORT", 10000))

# Adjust workers based on Render instance type (Free/Starter often have limited CPU)
# workers = multiprocessing.cpu_count() * 2 + 1
workers = int(os.environ.get("WEB_CONCURRENCY", 2)) # Start with 2 for free tier, Render might override

# Increase timeout for potentially long scraping tasks
timeout = 120 # seconds

# Optional: Logging configuration if needed
# errorlog = '-' # Log errors to stderr
# accesslog = '-' # Log access to stdout
# loglevel = 'info'