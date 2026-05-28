"""Start the fitness backend with HTTPS on 8443."""
import sys, os
sys.stdout.reconfigure(encoding='utf-8')
import uvicorn

cert_dir = os.path.dirname(os.path.abspath(__file__))
ssl_keyfile = os.path.join(cert_dir, 'key.pem')
ssl_certfile = os.path.join(cert_dir, 'cert.pem')
has_ssl = os.path.exists(ssl_keyfile) and os.path.exists(ssl_certfile)

if __name__ == "__main__":
    if has_ssl:
        print("HTTPS server on https://0.0.0.0:8443")
        uvicorn.run("main:app", host="0.0.0.0", port=8443,
                    ssl_keyfile=ssl_keyfile, ssl_certfile=ssl_certfile, reload=False)
    else:
        print("HTTP server on http://0.0.0.0:8080")
        uvicorn.run("main:app", host="0.0.0.0", port=8080, reload=False)
