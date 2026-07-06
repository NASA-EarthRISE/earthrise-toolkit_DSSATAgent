(function() {
    // Tab switching
    document.querySelectorAll('.tab').forEach(function(tab) {
        tab.addEventListener('click', function() {
            document.querySelectorAll('.tab').forEach(function(t) { t.classList.remove('active'); });
            document.querySelectorAll('.tab-content').forEach(function(c) { c.classList.remove('active'); });
            tab.classList.add('active');
            var target = document.getElementById(tab.dataset.tab);
            if (target) target.classList.add('active');
        });
    });

    // File viewer modal
    var modal = document.getElementById('file-viewer-modal');
    var title = document.getElementById('file-viewer-title');
    var content = document.getElementById('file-viewer-content');
    var experimentId = JSON.parse(document.getElementById('experiment-id').textContent);

    document.querySelectorAll('.view-file-btn').forEach(function(btn) {
        btn.addEventListener('click', function() {
            var fileType = btn.dataset.type;
            var fileKey = btn.dataset.key;
            var resultId = btn.dataset.resultId;
            title.textContent = fileKey;
            content.textContent = 'Loading...';
            modal.style.display = 'flex';

            var SUBPATH = window.SUBPATH || '';
            var url = SUBPATH + '/dssat/api/experiments/' + experimentId + '/results/' + resultId + '/files/' + fileType + '/' + fileKey + '/';
            fetch(url)
                .then(function(r) { return r.json(); })
                .then(function(data) {
                    content.textContent = data.content || data.error || 'No content';
                })
                .catch(function(err) {
                    content.textContent = 'Error loading file: ' + err;
                });
        });
    });

    if (document.getElementById('close-file-viewer')) {
        document.getElementById('close-file-viewer').addEventListener('click', function() {
            modal.style.display = 'none';
        });
    }
    if (modal) {
        modal.addEventListener('click', function(e) {
            if (e.target === modal) modal.style.display = 'none';
        });
    }
})();
