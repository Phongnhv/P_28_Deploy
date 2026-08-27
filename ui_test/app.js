// DataPulse - Interactive Application Logic

let currentRole = 'steward';
let currentScreen = 'screen-2';
let anomalyChartInstance = null;
let trendChartInstance = null;

// Initialize on Load
document.addEventListener('DOMContentLoaded', () => {
  initCharts();
});

// Role Selection Logic
function selectRoleOption(role) {
  currentRole = role;
  document.getElementById('role-option-steward').classList.remove('selected');
  document.getElementById('role-option-viewer').classList.remove('selected');
  document.getElementById(`role-option-${role}`).classList.add('selected');
}

function handleLogin(e) {
  e.preventDefault();
  document.getElementById('screen-1').classList.remove('active');
  document.getElementById('screen-1').style.display = 'none';
  
  // Apply Role Permissions
  applyRolePermissions(currentRole);

  if (currentRole === 'viewer') {
    navigateTo('screen-11');
  } else {
    navigateTo('screen-2');
  }
}

function applyRolePermissions(role) {
  const roleBadge = document.getElementById('current-role-badge');
  const roleText = document.getElementById('current-role-text');
  const stewardOnlyElements = document.querySelectorAll('.steward-only');

  if (role === 'viewer') {
    roleBadge.className = 'role-tag role-viewer';
    roleText.innerText = 'Role: Viewer (Read-Only)';
    stewardOnlyElements.forEach(el => el.style.display = 'none');
  } else {
    roleBadge.className = 'role-tag role-steward';
    roleText.innerText = 'Role: Data Steward (Full Access)';
    stewardOnlyElements.forEach(el => el.style.display = '');
  }
}

// Navigation Handler
function navigateTo(screenId) {
  currentScreen = screenId;

  const loginScreen = document.getElementById('screen-1');

  // Handle Login Screen display
  if (screenId === 'screen-1') {
    loginScreen.style.display = 'block';
    loginScreen.classList.add('active');
    return;
  } else {
    loginScreen.style.display = 'none';
    loginScreen.classList.remove('active');
  }

  // Update active screen
  document.querySelectorAll('.screen').forEach(s => s.classList.remove('active'));
  const targetScreen = document.getElementById(screenId);
  if (targetScreen) targetScreen.classList.add('active');

  // Update Sidebar active state
  document.querySelectorAll('.nav-item').forEach(item => item.classList.remove('active'));
  
  const navMap = {
    'screen-2': 'nav-dashboard',
    'screen-3': 'nav-catalog',
    'screen-4': 'nav-profiling',
    'screen-5': 'nav-rules',
    'screen-7': 'nav-execution',
    'screen-8': 'nav-anomalies',
    'screen-10': 'nav-trends',
    'screen-11': 'nav-dashboard'
  };

  if (navMap[screenId]) {
    const activeNav = document.getElementById(navMap[screenId]);
    if (activeNav) activeNav.classList.add('active');
  }

  // Refresh charts if entering chart screens
  if (screenId === 'screen-8') setTimeout(initAnomalyChart, 100);
  if (screenId === 'screen-10') setTimeout(initTrendChart, 100);
}

// Profiling Workflow Trigger
function startProfiling(tableName) {
  navigateTo('screen-4');
}

// HITL Rule Actions
function approveRule(ruleId) {
  const statusElem = document.getElementById(`status-rule-${ruleId}`);
  if (statusElem) {
    statusElem.className = 'tag tag-success';
    statusElem.innerText = 'Approved';
  }
}

function rejectRule(ruleId) {
  const statusElem = document.getElementById(`status-rule-${ruleId}`);
  if (statusElem) {
    statusElem.className = 'tag tag-error';
    statusElem.innerText = 'Rejected';
  }
}

function batchApprove() {
  approveRule(1);
  approveRule(2);
  alert('All 12 AI suggested rules have been approved by Data Steward!');
}

// Modal Handlers
function openEditModal(colName) {
  document.getElementById('edit-column-name').innerText = colName || 'fare_amount';
  document.getElementById('modal-rule-edit').classList.add('active');
}

function openDiagnosisModal() {
  document.getElementById('modal-ai-diagnosis').classList.add('active');
}

function closeModal(modalId) {
  document.getElementById(modalId).classList.remove('active');
}

function saveRuleEdit() {
  closeModal('modal-rule-edit');
  alert('Rule threshold updated successfully!');
}

// Chart Initializations
function initCharts() {
  initAnomalyChart();
  initTrendChart();
}

function initAnomalyChart() {
  const ctx = document.getElementById('anomalyChart');
  if (!ctx) return;

  if (anomalyChartInstance) anomalyChartInstance.destroy();

  anomalyChartInstance = new Chart(ctx, {
    type: 'line',
    data: {
      labels: ['Mon 00:00', 'Mon 06:00', 'Mon 12:00', 'Mon 18:00', 'Tue 00:00', 'Tue 06:00', 'Tue 12:00'],
      datasets: [{
        label: 'Data Quality Health Score (%)',
        data: [98.2, 97.5, 98.0, 84.2, 87.4, 88.0, 87.4],
        borderColor: '#1677ff',
        backgroundColor: 'rgba(22, 119, 255, 0.1)',
        fill: true,
        tension: 0.3,
        pointBackgroundColor: ['#52c41a', '#52c41a', '#52c41a', '#ff4d4f', '#faad14', '#52c41a', '#faad14'],
        pointRadius: 6
      }]
    },
    options: {
      responsive: true,
      plugins: { legend: { display: false } },
      scales: {
        y: { min: 70, max: 100, grid: { color: '#334155' }, ticks: { color: '#94a3b8' } },
        x: { grid: { color: '#334155' }, ticks: { color: '#94a3b8' } }
      }
    }
  });
}

function initTrendChart() {
  const ctx = document.getElementById('trendChart');
  if (!ctx) return;

  if (trendChartInstance) trendChartInstance.destroy();

  trendChartInstance = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: ['Week 1', 'Week 2', 'Week 3', 'Week 4'],
      datasets: [
        {
          label: 'Passed Rules',
          data: [1200, 1350, 1400, 1420],
          backgroundColor: '#52c41a'
        },
        {
          label: 'Failed Rules',
          data: [45, 30, 22, 18],
          backgroundColor: '#ff4d4f'
        }
      ]
    },
    options: {
      responsive: true,
      plugins: { legend: { labels: { color: '#f8fafc' } } },
      scales: {
        y: { grid: { color: '#334155' }, ticks: { color: '#94a3b8' } },
        x: { grid: { color: '#334155' }, ticks: { color: '#94a3b8' } }
      }
    }
  });
}
