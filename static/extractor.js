        const fileInput = document.getElementById('fileInput');
        const fileLabel = document.getElementById('fileLabel');
        const fileName = document.getElementById('fileName');
        const thresholdSlider = document.getElementById('threshold');
        const thresholdValue = document.getElementById('thresholdValue');
        const uploadForm = document.getElementById('uploadForm');
        const loading = document.getElementById('loading');
        const results = document.getElementById('results');
        const errorMsg = document.getElementById('errorMsg');
        const downloadBtn = document.getElementById('downloadBtn');

        let extractedInsights = [];
        let totalLinesCount = 0;
        let methodName = 'Keyword';

        fileInput.addEventListener('change', (e) => {
            const file = e.target.files[0];
            if (file) {
                fileName.textContent = file.name;
                fileLabel.classList.add('has-file');
            }
        });

        thresholdSlider.addEventListener('input', (e) => {
            thresholdValue.textContent = e.target.value;
        });

        uploadForm.addEventListener('submit', async (e) => {
            e.preventDefault();
            
            const file = fileInput.files[0];
            if (!file) {
                showError('Please select a file');
                return;
            }

            errorMsg.classList.remove('active');
            results.classList.remove('active');
            loading.classList.add('active');

            try {
                const text = await file.text();
                const threshold = parseFloat(thresholdSlider.value);
                
                const insights = extractInsightsByKeyword(text, threshold);

                extractedInsights = insights;
                totalLinesCount = text.split('\n').filter(l => l.trim()).length;
                methodName = 'Keyword';
                
                displayResults(insights, totalLinesCount, methodName);
                
            } catch (error) {
                showError('Error processing file: ' + error.message);
            } finally {
                loading.classList.remove('active');
            }
        });

        // Honest naming: this is keyword substring matching, not BERT/embeddings.
        // The relevance score is DETERMINISTIC -- identical input always yields the
        // same score and ordering (the previous Math.random() variation made the
        // score and sort order change on every run for the same file).
        function extractInsightsByKeyword(text, threshold) {
            const keywords = [
                'alignment', 'safety', 'ethics', 'bias', 'fairness', 'transparency',
                'interpretability', 'robustness', 'security', 'privacy', 'regulation',
                'governance', 'accountability', 'risk', 'control', 'autonomous',
                'superintelligence', 'AGI', 'existential', 'reward', 'objective',
                'optimization', 'misalignment', 'deception', 'adversarial', 'explainable'
            ];

            const lines = text.split('\n').filter(l => l.trim());
            const metadataPattern = /^(\[.*?\]\s+\w+:)\s+(.*)$/;
            const insights = [];

            for (let line of lines) {
                line = line.trim();
                if (!line) continue;

                const match = line.match(metadataPattern);
                const message = match ? match[2] : line;
                
                const lowerMessage = message.toLowerCase();
                const matchedKeywords = [];

                for (const keyword of keywords) {
                    if (lowerMessage.includes(keyword.toLowerCase())) {
                        matchedKeywords.push(keyword);
                    }
                }

                if (matchedKeywords.length > 0) {
                    const distinct = [...new Set(matchedKeywords)];
                    // Deterministic relevance: 0.35 base for a single matched
                    // concept, +0.07 per additional distinct concept, capped at
                    // 0.95. Monotonic in concept count, reproducible run-to-run.
                    const score = Math.min(0.35 + 0.07 * (distinct.length - 1), 0.95);
                    if (score >= threshold) {
                        insights.push({
                            text: line,
                            score: score,
                            keywords: distinct.slice(0, 3)
                        });
                    }
                }
            }

            insights.sort((a, b) => b.score - a.score);
            return insights;
        }

        function displayResults(insights, totalLines, method) {
            document.getElementById('insightCount').textContent = insights.length;
            document.getElementById('totalLines').textContent = totalLines;
            document.getElementById('methodUsed').textContent = method;

            const insightsList = document.getElementById('insightsList');
            insightsList.innerHTML = '';

            if (insights.length === 0) {
                insightsList.innerHTML = '<p style="text-align: center; color: var(--color-text-muted); padding: 40px;">No insights found. Try lowering the threshold.</p>';
            } else {
                insights.forEach((insight, idx) => {
                    const item = document.createElement('div');
                    item.className = 'insight-item';
                    item.innerHTML = `
                        <div class="insight-header">
                            <span class="insight-number">Insight #${idx + 1}</span>
                            <span class="insight-score">Relevance: ${insight.score.toFixed(2)}</span>
                        </div>
                        <div class="insight-keywords">Matched concepts: ${escapeHtml(insight.keywords.join(', '))}</div>
                        <div class="insight-text">${escapeHtml(insight.text)}</div>
                    `;
                    insightsList.appendChild(item);
                });
            }

            results.classList.add('active');
        }

        function showError(message) {
            errorMsg.textContent = message;
            errorMsg.classList.add('active');
        }

        function escapeHtml(text) {
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        }

        downloadBtn.addEventListener('click', () => {
            const markdown = generateMarkdown(extractedInsights, totalLinesCount, methodName);
            const blob = new Blob([markdown], { type: 'text/markdown' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `insights_${methodName.toLowerCase()}_${Date.now()}.md`;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(url);
        });

        function generateMarkdown(insights, totalLines, method) {
            let md = '# Insights from AI Thread\n\n';
            md += `## Extraction Method: ${method.toUpperCase()}\n\n`;
            md += '## Summary\n';
            md += `Extracted **${insights.length}** insights from **${totalLines}** lines.\n\n`;
            md += '**Keyword matching**: Lines containing AI-safety keywords, ranked by distinct-concept count.\n\n';
            md += '## Extracted Insights\n\n';

            insights.forEach((insight, idx) => {
                md += `### Insight ${idx + 1} (Relevance: ${insight.score.toFixed(2)})\n`;
                md += `**Matched concepts**: ${insight.keywords.join(', ')}\n\n`;
                md += `> ${insight.text}\n\n`;
            });

            return md;
        }
