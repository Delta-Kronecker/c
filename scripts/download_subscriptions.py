name: Clash Config Testing - Enhanced

on:
  # Run daily at 00:00 UTC
  schedule:
    - cron: '0 0 * * *'

  # Allow manual trigger
  workflow_dispatch:
    inputs:
      test_workers:
        description: 'Number of parallel test workers'
        required: false
        default: '20'
      test_timeout:
        description: 'Test timeout in seconds'
        required: false
        default: '10'

  # Run on push to main branch
  push:
    branches:
      - main
    paths:
      - 'sub.txt'
      - 'scripts/**'
      - '.github/workflows/clash-config-test.yml'

jobs:
  test-configs:
    runs-on: ubuntu-latest
    timeout-minutes: 60

    steps:
    - name: Checkout repository
      uses: actions/checkout@v4
      with:
        fetch-depth: 0

    - name: Set up Python
      uses: actions/setup-python@v5
      with:
        python-version: '3.11'
        cache: 'pip'

    - name: Install Python dependencies
      run: |
        python -m pip install --upgrade pip
        pip install requests pyyaml
        echo "✓ Python dependencies installed"

    - name: Download and install Clash Meta
      run: |
        # Download Clash Meta (mihomo)
        CLASH_VERSION="v1.18.0"
        CLASH_URL="https://github.com/MetaCubeX/mihomo/releases/download/${CLASH_VERSION}/mihomo-linux-amd64-${CLASH_VERSION}.gz"
        
        echo "📥 Downloading Clash Meta ${CLASH_VERSION}..."
        wget -q "${CLASH_URL}" -O clash.gz
        
        echo "📦 Extracting..."
        gunzip clash.gz
        chmod +x clash
        
        # Move to local bin
        mkdir -p ~/.local/bin
        mv clash ~/.local/bin/clash
        echo "$HOME/.local/bin" >> $GITHUB_PATH
        
        # Verify installation
        ~/.local/bin/clash -v
        echo "✓ Clash Meta installed successfully"

    - name: Download subscriptions
      id: download
      run: |
        cd scripts
        python download_subscriptions.py
        
        # Check if download was successful
        if [ -f "../temp_configs/parsed_proxies.json" ]; then
          PROXY_COUNT=$(cat ../temp_configs/parsed_proxies.json | python -c "import sys, json; print(len(json.load(sys.stdin)))")
          echo "proxy_count=$PROXY_COUNT" >> $GITHUB_OUTPUT
          echo "✓ Downloaded $PROXY_COUNT proxies"
        else
          echo "✗ Download failed"
          exit 1
        fi
      continue-on-error: false

    - name: Test configs
      id: test
      env:
        TEST_WORKERS: ${{ github.event.inputs.test_workers || '20' }}
        TEST_TIMEOUT: ${{ github.event.inputs.test_timeout || '10' }}
      run: |
        cd scripts
        python test_configs.py
        
        # Check if testing was successful
        if [ -f "../working_configs/metadata.json" ]; then
          WORKING=$(cat ../working_configs/metadata.json | python -c "import sys, json; print(json.load(sys.stdin)['total_working'])")
          echo "working_count=$WORKING" >> $GITHUB_OUTPUT
          echo "✓ Found $WORKING working proxies"
        else
          echo "working_count=0" >> $GITHUB_OUTPUT
          echo "⚠ No working proxies found"
        fi
      continue-on-error: true

    - name: Generate detailed summary
      if: always()
      run: |
        echo "# 🔍 Clash Config Test Results" >> $GITHUB_STEP_SUMMARY
        echo "" >> $GITHUB_STEP_SUMMARY
        echo "**Date:** $(date -u '+%Y-%m-%d %H:%M:%S UTC')" >> $GITHUB_STEP_SUMMARY
        echo "" >> $GITHUB_STEP_SUMMARY
        
        if [ -f "working_configs/metadata.json" ]; then
          echo "## ✅ Test Successful" >> $GITHUB_STEP_SUMMARY
          echo "" >> $GITHUB_STEP_SUMMARY
          
          # Parse metadata
          TOTAL=$(cat working_configs/metadata.json | python -c "import sys, json; print(json.load(sys.stdin)['total_working'])")
          AVG_LATENCY=$(cat working_configs/metadata.json | python -c "import sys, json; print(json.load(sys.stdin)['latency']['average'])")
          
          echo "### 📊 Statistics" >> $GITHUB_STEP_SUMMARY
          echo "" >> $GITHUB_STEP_SUMMARY
          echo "- **Total Working Proxies:** $TOTAL" >> $GITHUB_STEP_SUMMARY
          echo "- **Average Latency:** ${AVG_LATENCY}ms" >> $GITHUB_STEP_SUMMARY
          echo "" >> $GITHUB_STEP_SUMMARY
          
          echo "### 📋 By Protocol" >> $GITHUB_STEP_SUMMARY
          echo "" >> $GITHUB_STEP_SUMMARY
          cat working_configs/metadata.json | python -c "
import sys, json
data = json.load(sys.stdin)
for proto, count in sorted(data['by_protocol'].items(), key=lambda x: -x[1]):
    print(f'- **{proto}:** {count}')
" >> $GITHUB_STEP_SUMMARY
          
          echo "" >> $GITHUB_STEP_SUMMARY
          echo "### 📦 Output Files" >> $GITHUB_STEP_SUMMARY
          echo "" >> $GITHUB_STEP_SUMMARY
          echo "- \`working_configs/all_working.txt\` - All working proxies" >> $GITHUB_STEP_SUMMARY
          echo "- \`working_configs/all_working_base64.txt\` - Base64 encoded" >> $GITHUB_STEP_SUMMARY
          echo "- \`working_configs/clash_config.yaml\` - Ready-to-use Clash config" >> $GITHUB_STEP_SUMMARY
          echo "- \`working_configs/by_protocol/\` - Grouped by protocol" >> $GITHUB_STEP_SUMMARY
          
        elif [ -f "temp_configs/download_stats.json" ]; then
          echo "## ⚠️ Testing Failed" >> $GITHUB_STEP_SUMMARY
          echo "" >> $GITHUB_STEP_SUMMARY
          echo "Download completed but no proxies passed connectivity test." >> $GITHUB_STEP_SUMMARY
          
          DOWNLOADED=$(cat temp_configs/download_stats.json | python -c "import sys, json; print(json.load(sys.stdin)['total_parsed'])")
          echo "" >> $GITHUB_STEP_SUMMARY
          echo "- **Proxies Downloaded:** $DOWNLOADED" >> $GITHUB_STEP_SUMMARY
          echo "- **Working Proxies:** 0" >> $GITHUB_STEP_SUMMARY
          
        else
          echo "## ❌ Download Failed" >> $GITHUB_STEP_SUMMARY
          echo "" >> $GITHUB_STEP_SUMMARY
          echo "Failed to download subscription data. Please check:" >> $GITHUB_STEP_SUMMARY
          echo "" >> $GITHUB_STEP_SUMMARY
          echo "1. Subscription URLs in \`sub.txt\`" >> $GITHUB_STEP_SUMMARY
          echo "2. Network connectivity" >> $GITHUB_STEP_SUMMARY
          echo "3. Subscription format validity" >> $GITHUB_STEP_SUMMARY
        fi

    - name: Commit and push results
      if: success() && steps.test.outputs.working_count != '0'
      run: |
        git config --local user.email "github-actions[bot]@users.noreply.github.com"
        git config --local user.name "github-actions[bot]"
        
        # Add working configs
        git add working_configs/
        
        # Check if there are changes
        if git diff --staged --quiet; then
          echo "📝 No changes to commit"
        else
          # Create detailed commit message
          TIMESTAMP=$(date -u '+%Y-%m-%d %H:%M:%S UTC')
          WORKING_COUNT="${{ steps.test.outputs.working_count }}"
          
          # Get protocol breakdown
          PROTOCOLS=$(cat working_configs/metadata.json | python -c "
import sys, json
data = json.load(sys.stdin)
protocols = ', '.join([f'{k}:{v}' for k, v in data['by_protocol'].items()])
print(protocols)
" 2>/dev/null || echo "N/A")
          
          # Get latency info
          AVG_LATENCY=$(cat working_configs/metadata.json | python -c "
import sys, json
data = json.load(sys.stdin)
print(f\"{data['latency']['average']:.0f}ms\")
" 2>/dev/null || echo "N/A")
          
          git commit -m "🔄 Update working configs - ${TIMESTAMP}" \
                     -m "" \
                     -m "📊 Statistics:" \
                     -m "- Working Proxies: ${WORKING_COUNT}" \
                     -m "- Average Latency: ${AVG_LATENCY}" \
                     -m "- Protocols: ${PROTOCOLS}" \
                     -m "" \
                     -m "Generated by automated testing workflow"
          
          git push
          echo "✅ Changes committed and pushed"
        fi
      env:
        GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}

    - name: Upload test artifacts
      if: always()
      uses: actions/upload-artifact@v4
      with:
        name: test-results-${{ github.run_number }}
        path: |
          working_configs/
          temp_configs/*.json
          temp_configs/*.txt
        retention-days: 7
        compression-level: 9

    - name: Upload working configs as release asset
      if: success() && steps.test.outputs.working_count != '0'
      uses: actions/upload-artifact@v4
      with:
        name: working-configs-latest
        path: |
          working_configs/all_working.txt
          working_configs/all_working_base64.txt
          working_configs/clash_config.yaml
          working_configs/metadata.json
        retention-days: 30

    - name: Create summary badge data
      if: always()
      run: |
        mkdir -p badges
        WORKING="${{ steps.test.outputs.working_count }}"
        TOTAL="${{ steps.download.outputs.proxy_count }}"
        
        if [ -n "$WORKING" ] && [ -n "$TOTAL" ] && [ "$TOTAL" != "0" ]; then
          SUCCESS_RATE=$(python -c "print(f'{($WORKING / $TOTAL * 100):.1f}')" 2>/dev/null || echo "0")
          echo "{\"schemaVersion\": 1, \"label\": \"proxies\", \"message\": \"${WORKING}/${TOTAL} (${SUCCESS_RATE}%)\", \"color\": \"green\"}" > badges/status.json
        else
          echo "{\"schemaVersion\": 1, \"label\": \"proxies\", \"message\": \"failed\", \"color\": \"red\"}" > badges/status.json
        fi
        
        cat badges/status.json

    - name: Clean up temporary files
      if: always()
      run: |
        # Remove temporary config files but keep the results
        rm -rf temp_configs/*.yaml
        rm -rf temp_configs/config_*.yaml
        
        # Clean up old test artifacts
        find temp_configs -name "test_config_*.yaml" -mtime +1 -delete 2>/dev/null || true
        
        echo "🧹 Cleanup complete"

    - name: Post-test validation
      if: success()
      run: |
        echo "🔍 Validating output files..."
        
        # Check all expected files exist
        EXPECTED_FILES=(
          "working_configs/working_proxies.json"
          "working_configs/all_working.txt"
          "working_configs/clash_config.yaml"
          "working_configs/metadata.json"
        )
        
        MISSING=0
        for file in "${EXPECTED_FILES[@]}"; do
          if [ ! -f "$file" ]; then
            echo "❌ Missing: $file"
            MISSING=$((MISSING + 1))
          else
            SIZE=$(stat -f%z "$file" 2>/dev/null || stat -c%s "$file" 2>/dev/null)
            echo "✅ $file (${SIZE} bytes)"
          fi
        done
        
        if [ $MISSING -gt 0 ]; then
          echo "⚠️  Warning: $MISSING expected files are missing"
        else
          echo "✅ All output files validated successfully"
        fi

    - name: Send notification on failure
      if: failure()
      run: |
        echo "::warning::Clash config testing workflow failed. Please check the logs for details."