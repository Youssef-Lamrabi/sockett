// Global state
let currentUser = null;
let authToken = null;

// API configuration
const API_BASE = window.location.origin;

// Utility functions
function showMessage(message, type = 'success') {
    const messageEl = document.getElementById('message');
    if (messageEl) {
        messageEl.textContent = message;
        messageEl.className = `message ${type}`;
        setTimeout(() => {
            messageEl.textContent = '';
            messageEl.className = 'message';
        }, 5000);
    }
}

function setAuthToken(token) {
    authToken = token;
    localStorage.setItem('authToken', token);
}

function getAuthToken() {
    return authToken || localStorage.getItem('authToken');
}

function clearAuth() {
    authToken = null;
    localStorage.removeItem('authToken');
    currentUser = null;
}

async function apiCall(endpoint, options = {}) {
    const token = getAuthToken();
    const config = {
        headers: {
            'Content-Type': 'application/json',
            ...(token && { 'Authorization': `Bearer ${token}` }),
            ...options.headers
        },
        ...options
    };
    
    const response = await fetch(`${API_BASE}${endpoint}`, config);
    
    if (response.status === 401) {
        clearAuth();
        window.location.href = '/';
        return;
    }
    
    if (!response.ok) {
        const error = await response.text();
        throw new Error(error);
    }
    
    return response.json();
}

// Auth functions
function switchTab(tab) {
    document.querySelectorAll('.tab-btn').forEach(btn => btn.classList.remove('active'));
    document.querySelectorAll('.auth-form').forEach(form => form.classList.remove('active'));
    
    document.querySelector(`[onclick="switchTab('${tab}')"]`).classList.add('active');
    document.getElementById(`${tab}Form`).classList.add('active');
}

async function login(event) {
    event.preventDefault();
    const formData = new FormData(event.target);
    const data = {
        username: formData.get('email'),
        password: formData.get('password')
    };
    
    try {
        const response = await fetch(`${API_BASE}/auth/login`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
            body: new URLSearchParams(data)
        });
        
        if (response.ok) {
            const result = await response.json();
            setAuthToken(result.access_token);
            window.location.href = '/dashboard';
        } else {
            const error = await response.text();
            showMessage(error, 'error');
        }
    } catch (error) {
        showMessage('Login failed: ' + error.message, 'error');
    }
}

async function register(event) {
    event.preventDefault();
    const formData = new FormData(event.target);
    const data = {
        email: formData.get('email'),
        full_name: formData.get('full_name'),
        password: formData.get('password'),
        role: formData.get('role')
    };
    
    try {
        const result = await apiCall('/auth/register', {
            method: 'POST',
            body: JSON.stringify(data)
        });
        showMessage('Registration successful! You can now login.');
        switchTab('login');
    } catch (error) {
        showMessage('Registration failed: ' + error.message, 'error');
    }
}

function logout() {
    clearAuth();
    window.location.href = '/';
}

// Dashboard functions
function showTab(tabName) {
    document.querySelectorAll('.tab-content').forEach(content => content.classList.remove('active'));
    document.querySelectorAll('.nav-btn').forEach(btn => btn.classList.remove('active'));
    
    document.getElementById(`${tabName}Content`).classList.add('active');
    document.querySelector(`[onclick="showTab('${tabName}')"]`).classList.add('active');
    
    // Load content based on tab
    if (tabName === 'review') {
        loadPendingQAs();
    } else if (tabName === 'ready') {
        loadReadyQAs();
    }
}

async function loadUserInfo() {
    try {
        // Get user info
        const userInfo = await apiCall('/auth/me');
        console.log('Raw user info from API:', userInfo); // Debug log
        
        const userName = userInfo.full_name || userInfo.email.split('@')[0];
        const role = userInfo.role;
        const initials = userName.split(' ').map(n => n[0]).join('').toUpperCase().slice(0, 2);
        
        console.log('Processed user info:', { userName, role, initials }); // Debug log
        
        document.getElementById('userName').textContent = userName;
        
        // Only set role badge if element exists (not commented out)
        const roleBadge = document.getElementById('userRole');
        if (roleBadge) {
            roleBadge.textContent = initials;
            roleBadge.title = role;
        }
        
        // Show/hide tabs based on role
        console.log('Checking role:', role, 'Type:', typeof role); // Debug log
        if (role === 'provider') {
            console.log('Showing provider tabs'); // Debug log
            document.getElementById('uploadTab').style.display = 'block';
            document.getElementById('readyTab').style.display = 'block';
            
            // Auto-switch to Upload tab for providers
            showTab('upload');
        } 
        else {
            console.log('Hiding provider tabs for role:', role); // Debug log
            document.getElementById('uploadTab').style.display = 'none';
            document.getElementById('readyTab').style.display = 'none';
            
            // Keep Review tab active for annotators
            showTab('review');
        }
        
        // Load annotation count
        await loadAnnotationCount();
        
        currentUser = { role, name: userName, id: userInfo.id };
    } catch (error) {
        console.error('Failed to load user info:', error);
        showMessage('Failed to load user information: ' + error.message, 'error');
    }
}

async function loadAnnotationCount() {
    try {
        const stats = await apiCall('/review/stats');
        // This is a simple count - in a real app you'd want user-specific annotation count
        document.getElementById('annotationCount').textContent = stats.total;
    } catch (error) {
        console.error('Failed to load annotation count:', error);
    }
}

async function loadPendingQAs() {
    const qaList = document.getElementById('qaList');
    qaList.innerHTML = '<div class="loading">Loading QAs...</div>';
    
    try {
        const qas = await apiCall('/review/pending');
        currentList = qas;
        const countEl = document.getElementById('qaCount');
        countEl.textContent = `${qas.length} pending QAs`;
        
        if (qas.length === 0) {
            qaList.innerHTML = '<div class="loading">No pending QAs found.</div>';
            return;
        }
        
        // fetch categories once
        let categories = [];
        try { categories = await apiCall('/review/categories'); } catch {}

        qaList.innerHTML = qas.map((qa, idx) => {
            const annotatorHistory = qa.annotators && qa.annotators.length > 0 ? `
                <div class="annotator-history">
                    <strong>Annotated by:</strong>
                    ${qa.annotators.map(ann => `
                        <div class="annotator-item">
                            <div class="annotator-name">${ann.name} <span class="annotator-date">${new Date(ann.date).toLocaleDateString()}</span></div>
                            <div class="${(currentUser && ann.user_id === currentUser.id) ? 'proposal-self' : 'proposal-other'}">
                                <div><strong>Proposed Question:</strong> ${ann.edited_question || '(no change)'} </div>
                                <div><strong>Proposed Answer:</strong> ${ann.edited_answer || '(no change)'} </div>
                            </div>
                            <div>
                                <span class="annotator-score">${ann.score}</span>
                                <button class="btn btn-secondary btn-sm" onclick="supportAnnotation(${ann.annotation_id})">Support</button>
                            </div>
                        </div>
                    `).join('')}
                </div>
            ` : '';
            
            const categorySelect = categories.length ? `
                <div class="form-group">
                    <label>Category</label>
                    <select onchange="setCategory(${qa.id}, this.value)">
                        <option value="" ${!qa.category_id ? 'selected' : ''}>(none)</option>
                        ${categories.map(c => `<option value="${c.id}" ${qa.category_id===c.id?'selected':''}>${c.name}</option>`).join('')}
                    </select>
                </div>
            ` : '';

            return `
                <div class="qa-item">
                    <details style="margin:8px 0;"><summary>View source chunk</summary><pre style="white-space:pre-wrap; background:#f1f3f5; padding:10px; border-radius:6px;">${(qa.chunk_content || '').replace(/</g, '&lt;')}</pre></details>
                    <div class="qa-question">${qa.question}</div>
                    <div class="qa-answer">${qa.answer}</div>
                    <div class="qa-meta">
                        <span>ID: ${qa.id}</span>
                        <span>Created: ${new Date(qa.created_at).toLocaleDateString()}</span>
                        <span>Annotated by ${qa.annotator_count || 0}</span>
                    </div>
                    ${categorySelect}
                    ${annotatorHistory}
                    <div class="qa-actions">
                        <button class="btn btn-primary btn-sm" onclick="openReviewDrawer(${idx})">
                            <i class="fas fa-edit"></i> Review
                        </button>
                    </div>
                </div>
            `;
        }).join('');
    } catch (error) {
        qaList.innerHTML = `<div class="loading">Error loading QAs: ${error.message}</div>`;
    }
}

async function loadReadyQAs() {
    const readyList = document.getElementById('readyList');
    readyList.innerHTML = '<div class="loading">Loading ready QAs...</div>';
    
    try {
        // Load stats first
        const stats = await apiCall('/review/stats');
        document.getElementById('totalQAs').textContent = stats.total;
        document.getElementById('pendingQAs').textContent = stats.pending;
        document.getElementById('readyQAs').textContent = stats.ready;
        document.getElementById('rejectedQAs').textContent = stats.rejected;
        
        // Load ready QAs
        const qas = await apiCall('/provider/ready');
        
        if (qas.length === 0) {
            readyList.innerHTML = '<div class="loading">No ready QAs found.</div>';
            return;
        }
        
        readyList.innerHTML = qas.map(qa => `
            <div class="qa-item">
                <div class="qa-question">${qa.question}</div>
                <div class="qa-answer">${qa.answer}</div>
                <div class="qa-meta">
                    <span>ID: ${qa.id}</span>
                    <span>Created: ${new Date(qa.created_at).toLocaleDateString()}</span>
                </div>
            </div>
        `).join('');
    } catch (error) {
        readyList.innerHTML = `<div class="loading">Error loading ready QAs: ${error.message}</div>`;
    }
}

// Upload functions
async function uploadFile(event) {
    event.preventDefault();
    const fileInput = document.getElementById('fileInput');
    const resultBox = document.getElementById('uploadResult');
    
    if (!fileInput.files[0]) {
        showMessage('Please select a file', 'error');
        return;
    }
    
    const formData = new FormData();
    formData.append('f', fileInput.files[0]);
    
    resultBox.innerHTML = '<div class="loading">Processing file...</div>';
    
    try {
        const response = await fetch(`${API_BASE}/upload/file`, {
            method: 'POST',
            headers: { 'Authorization': `Bearer ${getAuthToken()}` },
            body: formData
        });
        
        if (response.ok) {
            const result = await response.json();
            resultBox.innerHTML = `
                <div class="message success">
                    <strong>Success!</strong><br>
                    Created ${result.chunks} chunks<br>
                    Generated ${result.qa_generated} QAs
                </div>
            `;
            fileInput.value = '';
        } else {
            const error = await response.text();
            resultBox.innerHTML = `<div class="message error">Error: ${error}</div>`;
        }
    } catch (error) {
        resultBox.innerHTML = `<div class="message error">Upload failed: ${error.message}</div>`;
    }
}

// Drawer functions
let currentIndex = -1;
let currentList = [];

function openReviewDrawer(idx) {
    currentIndex = idx;
    const qa = currentList[idx];
    const drawer = document.getElementById('reviewDrawer');
    document.getElementById('drawerQaId').value = qa.id;
    document.getElementById('drawerQuestion').value = qa.question;
    document.getElementById('drawerAnswer').value = qa.answer;
    document.getElementById('drawerScore').value = 0.7;
    document.getElementById('drawerScoreValue').textContent = '0.7';
    document.getElementById('drawerComment').value = '';
    document.getElementById('drawerChunk').textContent = qa.chunk_content || '';
    document.getElementById('drawerInitialQ').textContent = qa.question;
    document.getElementById('drawerInitialA').textContent = qa.answer;

    (async () => {
        let cats = [];
        try { cats = await apiCall('/review/categories'); } catch {}
        const sel = document.getElementById('drawerCategory');
        sel.innerHTML = '';
        const noneOpt = document.createElement('option');
        noneOpt.value = '';
        noneOpt.textContent = '(none)';
        sel.appendChild(noneOpt);
        cats.forEach(c => {
            const opt = document.createElement('option');
            opt.value = c.id;
            opt.textContent = c.name;
            if (qa.category_id === c.id) opt.selected = true;
            sel.appendChild(opt);
        });
        sel.onchange = () => setCategory(qa.id, sel.value);
    })();

    // render other proposals
    const propEl = document.getElementById('drawerProposals');
    propEl.innerHTML = (qa.annotators || []).map(ann => `
        <div class="annotator-item" style="flex-direction:column;">
            <div class="annotator-name">${ann.name} <span class="annotator-date">${new Date(ann.date).toLocaleDateString()}</span></div>
            <div class="${(currentUser && ann.user_id === currentUser.id) ? 'proposal-self' : 'proposal-other'}">
                <div><strong>Proposed Question:</strong> ${ann.edited_question || '(no change)'} </div>
                <div><strong>Proposed Answer:</strong> ${ann.edited_answer || '(no change)'} </div>
            </div>
            <div>
                <span class="annotator-score">${ann.score}</span>
                <button class="btn btn-secondary btn-sm" onclick="supportAnnotation(${ann.annotation_id})">Support</button>
            </div>
        </div>
    `).join('');

    drawer.classList.add('open');
    // document.getElementById('dashboardContent').style.marginRight = '420px';
    highlightCurrentCard();
}

function closeDrawer() {
    const drawer = document.getElementById('reviewDrawer');
    drawer.classList.remove('open');
    document.getElementById('dashboardContent').style.marginRight = '0';
    clearHighlight();
}

async function saveDraft() {
    const data = {
        qa_item_id: parseInt(document.getElementById('drawerQaId').value),
        edited_question: document.getElementById('drawerQuestion').value,
        edited_answer: document.getElementById('drawerAnswer').value,
        score: parseFloat(document.getElementById('drawerScore').value),
        comment: document.getElementById('drawerComment').value,
        validated: false
    };
    try {
        await apiCall('/review/annotate', { method: 'POST', body: JSON.stringify(data) });
        showDrawerMsg('Saved');
        loadPendingQAs();
    } catch (e) { showDrawerMsg('Save failed: ' + e.message, true); }
}

async function validateAndNext() {
    const data = {
        qa_item_id: parseInt(document.getElementById('drawerQaId').value),
        edited_question: document.getElementById('drawerQuestion').value,
        edited_answer: document.getElementById('drawerAnswer').value,
        score: parseFloat(document.getElementById('drawerScore').value),
        comment: document.getElementById('drawerComment').value,
        validated: true
    };
    try {
        await apiCall('/review/annotate', { method: 'POST', body: JSON.stringify(data) });
        nextItem();
    } catch (e) { showDrawerMsg('Validate failed: ' + e.message, true); }
}

function nextItem() {
    if (currentIndex < 0) return;
    const nextIdx = currentIndex + 1;
    if (nextIdx >= currentList.length) { closeDrawer(); loadPendingQAs(); return; }
    openReviewDrawer(nextIdx);
}

function showDrawerMsg(msg, isError=false) {
    const el = document.getElementById('drawerMessage');
    el.textContent = msg;
    el.className = 'message ' + (isError ? 'error' : 'success');
    setTimeout(() => { el.textContent=''; el.className='message'; }, 3000);
}

function highlightCurrentCard() {
    const cards = document.querySelectorAll('.qa-item');
    clearHighlight();
    if (currentIndex >= 0 && currentIndex < cards.length) {
        const el = cards[currentIndex];
        el.style.boxShadow = '0 0 0 2px #ffc107 inset';
    }
}

function clearHighlight() {
    document.querySelectorAll('.qa-item').forEach(el => {
        el.style.boxShadow = '';
    });
}

function exportQAs(format) {
    const url = `/provider/export/${format}`;
    const link = document.createElement('a');
    link.href = url;
    link.download = `ready_qas.${format}`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    showMessage(`Exporting QAs as ${format.toUpperCase()}...`);
}

// Category & Support actions
async function setCategory(qaId, categoryId) {
    try {
        await apiCall('/review/set_category', {
            method: 'POST',
            body: JSON.stringify({ qa_item_id: qaId, category_id: categoryId ? parseInt(categoryId) : null })
        });
        showMessage('Category updated');
    } catch (e) {
        showMessage('Failed to set category: ' + e.message, 'error');
    }
}

async function supportAnnotation(annotationId) {
    try {
        const res = await apiCall('/review/support', {
            method: 'POST',
            body: JSON.stringify({ annotation_id: annotationId, delta: 0.1 })
        });
        showMessage('Supported annotation');
        // remove the supported QA from queue (marked ready)
        if (res && res.qa_item_id) {
            currentList = currentList.filter(q => q.id !== res.qa_item_id);
            loadPendingQAs();
            if (currentIndex >= currentList.length) closeDrawer(); else highlightCurrentCard();
        } else {
            loadPendingQAs();
        }
    } catch (e) {
        showMessage('Failed to support: ' + e.message, 'error');
    }
}

// Event listeners
document.addEventListener('DOMContentLoaded', function() {
    // Check if we're on dashboard and user is logged in
    if (window.location.pathname === '/dashboard') {
        if (!getAuthToken()) {
            window.location.href = '/';
            return;
        }
        loadUserInfo();
    }
    
    // Form event listeners
    const loginForm = document.getElementById('loginForm');
    if (loginForm) {
        loginForm.addEventListener('submit', login);
    }
    
    const registerForm = document.getElementById('registerForm');
    if (registerForm) {
        registerForm.addEventListener('submit', register);
    }
    
    const uploadForm = document.getElementById('uploadForm');
    if (uploadForm) {
        uploadForm.addEventListener('submit', uploadFile);
    }
    
    const annotationForm = document.getElementById('annotationForm');
    if (annotationForm) {
        annotationForm.addEventListener('submit', saveAnnotation);
    }
    
    // Score slider
    const scoreSlider = document.getElementById('score');
    const scoreValue = document.getElementById('scoreValue');
    if (scoreSlider && scoreValue) {
        scoreSlider.addEventListener('input', function() {
            scoreValue.textContent = this.value;
        });
    }
    
    // Modal close on outside click
    const modal = document.getElementById('qaModal');
    if (modal) {
        window.addEventListener('click', function(event) {
            if (event.target === modal) {
                closeModal();
            }
        });
    }
});
