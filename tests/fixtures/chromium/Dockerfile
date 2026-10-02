FROM docker.io/library/node:24-bookworm-slim

ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
WORKDIR /opt/chromium-test

# Install only Chromium's headless shell and its system dependencies.
RUN npm install --ignore-scripts --no-audit --no-fund --save-exact playwright-core@1.63.0 \
    && node node_modules/playwright-core/cli.js install --with-deps --only-shell chromium \
    && npm cache clean --force \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1001 pwuser \
    && chmod -R a+rX /ms-playwright

COPY smoke.mjs ./
USER pwuser
CMD ["node", "smoke.mjs"]
