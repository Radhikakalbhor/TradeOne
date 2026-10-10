// Theme Management - Permanently Locked to Dark Mode
(function() {
  document.documentElement.setAttribute('data-theme', 'dark');
  localStorage.setItem('nd_theme', 'dark');
})();

function toggleTheme() {
  document.documentElement.setAttribute('data-theme', 'dark');
  localStorage.setItem('nd_theme', 'dark');
}

document.addEventListener('DOMContentLoaded', function() {
  document.documentElement.setAttribute('data-theme', 'dark');

  // Email persistence on login screen
  const emailInput = document.getElementById('login-email-input');
  if (emailInput) {
    const savedEmail = sessionStorage.getItem('nd_typed_email');
    if (savedEmail && !emailInput.value) {
      emailInput.value = savedEmail;
    }
    emailInput.addEventListener('input', function(e) {
      sessionStorage.setItem('nd_typed_email', e.target.value);
    });
  }

  // OTP Digits Auto-Advancing
  const otpInputs = document.querySelectorAll('.otp-digit');
  const fullOtpInput = document.getElementById('full-otp-input');
  const otpForm = document.getElementById('otp-form');

  if (otpInputs.length === 6 && fullOtpInput) {
    otpInputs[0].focus();
    let isSubmitting = false;

    function updateFullOtp() {
      let code = '';
      otpInputs.forEach(i => code += (i.value || ''));
      fullOtpInput.value = code;
    }

    otpInputs.forEach((input, index) => {
      input.addEventListener('input', (e) => {
        const val = e.target.value.replace(/[^0-9]/g, '');
        e.target.value = val ? val[val.length - 1] : '';

        if (e.target.value && index < 5) {
          otpInputs[index + 1].focus();
        }

        updateFullOtp();
        if (index === 5 && e.target.value) {
          const submitBtn = document.getElementById('btn-submit-otp');
          if (submitBtn) {
            submitBtn.focus();
          }
        }
      });

      input.addEventListener('keydown', (e) => {
        if (e.key === 'Backspace' && !e.target.value && index > 0) {
          otpInputs[index - 1].focus();
        }
      });

      input.addEventListener('paste', (e) => {
        e.preventDefault();
        const pastedData = (e.clipboardData || window.clipboardData).getData('text').replace(/[^0-9]/g, '');
        if (pastedData.length >= 6) {
          for (let i = 0; i < 6; i++) {
            otpInputs[i].value = pastedData[i];
          }
          otpInputs[5].focus();
          updateFullOtp();
          const submitBtn = document.getElementById('btn-submit-otp');
          if (submitBtn) {
            submitBtn.focus();
          }
        }
      });
    });

    if (otpForm) {
      otpForm.addEventListener('submit', function(e) {
        if (isSubmitting) {
          e.preventDefault();
          return;
        }

        updateFullOtp();
        const code = fullOtpInput ? fullOtpInput.value.trim() : '';
        if (!code || code.length !== 6 || !/^\d{6}$/.test(code)) {
          e.preventDefault();
          for (let i = 0; i < otpInputs.length; i++) {
            if (!otpInputs[i].value) {
              otpInputs[i].focus();
              break;
            }
          }
          return;
        }

        isSubmitting = true;
        const submitBtn = document.getElementById('btn-submit-otp');
        if (submitBtn) {
          submitBtn.disabled = true;
          submitBtn.textContent = 'Verifying...';
        }
      });
    }
  }

  // 45-Second Resend Countdown Timer
  const resendBtn = document.getElementById('resend-otp-btn');
  const resendTimerSpan = document.getElementById('resend-timer');
  if (resendBtn && resendTimerSpan) {
    let timeLeft = 45;
    resendBtn.disabled = true;
    resendBtn.style.opacity = '0.6';
    resendBtn.style.pointerEvents = 'none';
    resendTimerSpan.textContent = `(${timeLeft}s)`;

    const interval = setInterval(() => {
      timeLeft -= 1;
      if (timeLeft > 0) {
        resendTimerSpan.textContent = `(${timeLeft}s)`;
      } else {
        clearInterval(interval);
        resendBtn.disabled = false;
        resendBtn.style.opacity = '1';
        resendBtn.style.pointerEvents = 'auto';
        resendTimerSpan.textContent = '';
      }
    }, 1000);
  }
});

function copyToClipboard(text, elemId) {
  navigator.clipboard.writeText(text).then(() => {
    const el = document.getElementById(elemId);
    if (el) {
      const orig = el.textContent;
      el.textContent = 'Copied!';
      setTimeout(() => el.textContent = orig, 1500);
    }
  });
}

// Expandable Left-side Navigation Drawer
function toggleNavDrawer(forceState) {
  const drawer = document.getElementById('nav-drawer');
  const overlay = document.getElementById('nav-drawer-overlay');
  if (!drawer || !overlay) return;

  const isOpen = drawer.classList.contains('active');
  const shouldOpen = typeof forceState === 'boolean' ? forceState : !isOpen;

  if (shouldOpen) {
    drawer.classList.add('active');
    overlay.classList.add('active');
    document.body.style.overflow = 'hidden';
  } else {
    drawer.classList.remove('active');
    overlay.classList.remove('active');
    document.body.style.overflow = '';
  }
}

// Close drawer on Escape key
document.addEventListener('keydown', function(e) {
  if (e.key === 'Escape') {
    toggleNavDrawer(false);
  }
});

// ========================================================
// Aceternity UI Card Hover Effect System
// ========================================================
function initCardHoverEffects() {
  // 1. Interactive Cursor Spotlight on every card
  const cards = document.querySelectorAll('.card, .stat-card, .auth-card');
  cards.forEach(card => {
    card.addEventListener('mousemove', e => {
      const rect = card.getBoundingClientRect();
      const x = e.clientX - rect.left;
      const y = e.clientY - rect.top;
      card.style.setProperty('--mouse-x', `${x}px`);
      card.style.setProperty('--mouse-y', `${y}px`);
    });
  });

  // 2. Gliding Animated Hover Pill for Card Grids (Aceternity layoutId="hoverBackground")
  const gridContainers = document.querySelectorAll('.grid-2, .grid-3, .grid-4');
  gridContainers.forEach(grid => {
    const gridCards = grid.querySelectorAll(':scope > .card, :scope > .stat-card');
    if (gridCards.length < 2) return;

    let pill = grid.querySelector(':scope > .card-hover-pill');
    if (!pill) {
      pill = document.createElement('div');
      pill.className = 'card-hover-pill';
      grid.prepend(pill);
    }

    gridCards.forEach(card => {
      card.addEventListener('mouseenter', () => {
        pill.style.top = `${card.offsetTop}px`;
        pill.style.left = `${card.offsetLeft}px`;
        pill.style.width = `${card.offsetWidth}px`;
        pill.style.height = `${card.offsetHeight}px`;
        pill.style.opacity = '1';
      });
    });

    grid.addEventListener('mouseleave', () => {
      pill.style.opacity = '0';
    });
  });
}

// Aceternity FocusCards Effect on Navigation Drawer links
function initDrawerFocusCards() {
  const container = document.querySelector('.nav-drawer-links');
  if (!container) return;

  const links = container.querySelectorAll('.drawer-link');
  let rafId = null;

  links.forEach(link => {
    link.addEventListener('mouseenter', () => {
      if (rafId) cancelAnimationFrame(rafId);
      rafId = requestAnimationFrame(() => {
        container.classList.add('has-hover');
        links.forEach(other => {
          if (other !== link) {
            other.classList.add('is-dimmed');
          } else {
            other.classList.remove('is-dimmed');
          }
        });
      });
    });
  });

  container.addEventListener('mouseleave', () => {
    if (rafId) cancelAnimationFrame(rafId);
    rafId = requestAnimationFrame(() => {
      container.classList.remove('has-hover');
      links.forEach(l => l.classList.remove('is-dimmed'));
    });
  });
}

document.addEventListener('DOMContentLoaded', function() {
  initCardHoverEffects();
  initDrawerFocusCards();

  // Keyboard accessibility for Top Securities collapsible tab header
  const topSecuritiesHeader = document.getElementById('top-securities-header');
  if (topSecuritiesHeader) {
    topSecuritiesHeader.addEventListener('keydown', function(e) {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        toggleTopSecuritiesTab();
      }
    });
  }
});

// Interactive Collapsible Tab toggle for Top Securities
window.toggleTopSecuritiesTab = function() {
  const card = document.getElementById('top-securities-card');
  const btnText = document.getElementById('top-securities-btn-text');
  const header = document.getElementById('top-securities-header');
  if (!card) return;

  const isExpanded = card.classList.toggle('expanded');
  if (btnText) {
    btnText.textContent = isExpanded ? 'Collapse Table' : 'Expand Table';
  }
  if (header) {
    header.setAttribute('aria-expanded', isExpanded ? 'true' : 'false');
  }
};

window.addEventListener('resize', initCardHoverEffects);

// Interactive Shrunken Card toggle for Dashboard 60/40 view
window.toggleShrunkCard = function(cardId) {
  const card = document.getElementById(cardId);
  if (!card) return;
  const isExpanded = card.classList.toggle('is-expanded');
  const badge = card.querySelector('.shrunk-card-tap-badge');
  if (badge) {
    badge.textContent = isExpanded ? 'Tap to collapse' : 'Tap to expand';
  }
  const header = card.querySelector('.shrunk-card-header');
  if (header) {
    header.setAttribute('aria-expanded', isExpanded ? 'true' : 'false');
  }
};

// Graph Tab switcher
window.switchGraphTab = function(tabKey) {
  const buttons = document.querySelectorAll('.graph-tab-btn');
  const panels = document.querySelectorAll('.graph-panel');

  buttons.forEach(btn => {
    btn.classList.toggle('active', btn.getAttribute('data-tab') === tabKey);
  });

  panels.forEach(p => {
    p.classList.toggle('active', p.getAttribute('data-panel') === tabKey);
  });

  // Re-trigger layout resize for Chart.js
  setTimeout(() => {
    window.dispatchEvent(new Event('resize'));
  }, 50);
};






