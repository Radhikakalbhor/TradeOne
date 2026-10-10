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

// Interactive Shrunken Card toggle for Dashboard 60/40 view (kept for backwards compatibility)
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

// Aceternity Expandable Card Modal System
window.openExpandableCard = function(cardId) {
  const card = document.querySelector(`[data-card-id="${cardId}"]`);
  const backdrop = document.getElementById('aceternity-modal-backdrop');
  if (!card || !backdrop) return;

  const titleEl = card.querySelector('.compact-module-title, .square-card-title');
  const iconEl = card.querySelector('.compact-module-icon, .square-card-icon');
  const contentEl = card.querySelector('.square-card-content, .compact-module-content');
  const modalTitle = document.getElementById('aceternity-modal-title');
  const modalIcon = document.getElementById('aceternity-modal-icon');
  const modalBody = document.getElementById('aceternity-modal-body');

  if (modalTitle && titleEl) modalTitle.textContent = titleEl.textContent.trim();
  if (modalIcon && iconEl) modalIcon.innerHTML = iconEl.innerHTML;
  if (modalBody && contentEl) modalBody.innerHTML = contentEl.innerHTML;

  backdrop.style.display = 'flex';
  backdrop.setAttribute('aria-hidden', 'false');
  document.body.style.overflow = 'hidden';

  requestAnimationFrame(() => {
    backdrop.classList.add('is-active');
  });
};

window.closeExpandableCard = function(e) {
  const backdrop = document.getElementById('aceternity-modal-backdrop');
  if (!backdrop) return;

  backdrop.classList.remove('is-active');
  document.body.style.overflow = '';

  setTimeout(() => {
    if (!backdrop.classList.contains('is-active')) {
      backdrop.style.display = 'none';
      backdrop.setAttribute('aria-hidden', 'true');
      const modalBody = document.getElementById('aceternity-modal-body');
      if (modalBody) modalBody.innerHTML = '';
    }
  }, 240);
};

// Global escape key listener
document.addEventListener('keydown', function(event) {
  if (event.key === 'Escape') {
    const backdrop = document.getElementById('aceternity-modal-backdrop');
    if (backdrop && backdrop.style.display !== 'none' && backdrop.classList.contains('is-active')) {
      closeExpandableCard();
    }
  }
});

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

// Educational Disclaimer Flash Message in the Middle of Screen
let learnFlashTimer = null;

window.showLearnFlashMessage = function() {
  const backdrop = document.getElementById('learn-flash-backdrop');
  const flash = document.getElementById('learn-flash-message');
  const timerBar = document.getElementById('learn-flash-timer-bar');

  if (flash) {
    if (backdrop) {
      backdrop.style.display = 'block';
      requestAnimationFrame(() => {
        backdrop.style.opacity = '1';
      });
    }

    flash.style.display = 'block';
    requestAnimationFrame(() => {
      flash.style.opacity = '1';
      flash.style.transform = 'translate(-50%, -50%) scale(1)';
    });

    const ackBtn = document.getElementById('btn-ack-flash');
    if (ackBtn) ackBtn.focus();

    // Reset & animate auto-dismiss timer bar
    if (timerBar) {
      timerBar.style.transition = 'none';
      timerBar.style.width = '100%';
      requestAnimationFrame(() => {
        requestAnimationFrame(() => {
          timerBar.style.transition = 'width 7s linear';
          timerBar.style.width = '0%';
        });
      });
    }

    // Auto-dismiss after 7 seconds
    if (learnFlashTimer) clearTimeout(learnFlashTimer);
    learnFlashTimer = setTimeout(() => {
      closeLearnFlashMessage();
    }, 7000);
  }
};

window.closeLearnFlashMessage = function() {
  if (learnFlashTimer) {
    clearTimeout(learnFlashTimer);
    learnFlashTimer = null;
  }

  const backdrop = document.getElementById('learn-flash-backdrop');
  const flash = document.getElementById('learn-flash-message');

  if (flash) {
    flash.style.opacity = '0';
    flash.style.transform = 'translate(-50%, -50%) scale(0.92)';
    if (backdrop) backdrop.style.opacity = '0';

    setTimeout(() => {
      flash.style.display = 'none';
      if (backdrop) backdrop.style.display = 'none';
    }, 300);

    sessionStorage.setItem('learn_disclaimer_acked', 'true');
  }
};

// Aliases for compatibility
window.showLearnDisclaimerModal = window.showLearnFlashMessage;
window.closeLearnDisclaimerModal = window.closeLearnFlashMessage;

document.addEventListener('DOMContentLoaded', function() {
  const path = window.location.pathname;
  if (path.startsWith('/learn')) {
    document.addEventListener('keydown', function(e) {
      if (e.key === 'Escape') {
        closeLearnFlashMessage();
      }
    });

    const isLearnRoot = (path === '/learn' || path === '/learn/');
    const acked = sessionStorage.getItem('learn_disclaimer_acked');

    if (isLearnRoot || !acked) {
      setTimeout(showLearnFlashMessage, 150);
    }
  } else {
    // Leaving learn section resets acknowledgement so returning later re-triggers popup
    sessionStorage.removeItem('learn_disclaimer_acked');
  }
});

// -------------------------------------------------------------
// Aceternity Sparkles Background Canvas Effect
// -------------------------------------------------------------
(function initSparkles() {
  function start() {
    const canvas = document.getElementById('sparkles-canvas');
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const minSize = 0.6;
    const maxSize = 1.4;
    const particleColor = '#FFFFFF';
    const density = 100; // Aceternity particleDensity

    let width = 0;
    let height = 0;
    let particles = [];
    let animId = null;

    function resize() {
      width = canvas.width = window.innerWidth;
      height = canvas.height = window.innerHeight;
      createParticles();
    }

    function createParticles() {
      const count = Math.max(50, Math.floor((width * height) / (1000000 / density)));
      particles = [];
      for (let i = 0; i < count; i++) {
        particles.push({
          x: Math.random() * width,
          y: Math.random() * height,
          size: Math.random() * (maxSize - minSize) + minSize,
          speedX: (Math.random() - 0.5) * 0.35,
          speedY: (Math.random() - 0.5) * 0.35,
          alpha: Math.random() * 0.75 + 0.15,
          fadeSpeed: Math.random() * 0.015 + 0.005,
          fadingIn: Math.random() > 0.5,
        });
      }
    }

    function render() {
      ctx.clearRect(0, 0, width, height);

      for (let i = 0; i < particles.length; i++) {
        const p = particles[i];

        if (p.fadingIn) {
          p.alpha += p.fadeSpeed;
          if (p.alpha >= 0.95) p.fadingIn = false;
        } else {
          p.alpha -= p.fadeSpeed;
          if (p.alpha <= 0.08) p.fadingIn = true;
        }

        p.x += p.speedX;
        p.y += p.speedY;

        if (p.x < 0) p.x = width;
        else if (p.x > width) p.x = 0;
        if (p.y < 0) p.y = height;
        else if (p.y > height) p.y = 0;

        ctx.save();
        ctx.globalAlpha = p.alpha;
        ctx.fillStyle = particleColor;
        ctx.shadowBlur = 4;
        ctx.shadowColor = particleColor;
        ctx.beginPath();
        ctx.arc(p.x, p.y, p.size, 0, Math.PI * 2);
        ctx.fill();
        ctx.restore();
      }

      animId = requestAnimationFrame(render);
    }

    window.addEventListener('resize', resize);
    resize();
    render();

    document.addEventListener('visibilitychange', () => {
      if (document.hidden) {
        if (animId) cancelAnimationFrame(animId);
      } else {
        animId = requestAnimationFrame(render);
      }
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start);
  } else {
    start();
  }
})();







