// TradeOne Core Client Scripts

// Theme Management
(function() {
  const savedTheme = localStorage.getItem('nd_theme') || 
    (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
  document.documentElement.setAttribute('data-theme', savedTheme);
})();

function toggleTheme() {
  const current = document.documentElement.getAttribute('data-theme') || 'light';
  const target = current === 'dark' ? 'light' : 'dark';
  document.documentElement.setAttribute('data-theme', target);
  localStorage.setItem('nd_theme', target);
  updateThemeIcons(target);
}

function updateThemeIcons(theme) {
  const moonIcon = document.getElementById('theme-moon');
  const sunIcon = document.getElementById('theme-sun');
  if (moonIcon && sunIcon) {
    if (theme === 'dark') {
      moonIcon.style.display = 'none';
      sunIcon.style.display = 'block';
    } else {
      moonIcon.style.display = 'block';
      sunIcon.style.display = 'none';
    }
  }
}

document.addEventListener('DOMContentLoaded', function() {
  const currentTheme = document.documentElement.getAttribute('data-theme') || 'light';
  updateThemeIcons(currentTheme);

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

// Copy to clipboard utility
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
