/**
 * Kenya Election 2027 — Frontend App
 * Cross-browser ES5-compatible (with modern features gracefully)
 * Handles: candidate selection → phone registration → M-Pesa STK push →
 *          payment polling → vote casting
 */

(function () {
  'use strict';

  /* ─── Config ──────────────────────────────────────────────────── */
  var API_BASE = '';  // same-origin; change to 'https://yourdomain.com' for external

  /* ─── State ───────────────────────────────────────────────────── */
  var state = {
    selectedTicket: null,
    phoneNumber: '',
    normalizedPhone: '',
    mpesaReceipt: null,
    voteId: null,
    pollInterval: null,
    pollCountdown: null,
    currentStep: 0
  };

  var TICKETS = [
    {
      id: 'ruto-kindiki',
      names: 'William Ruto & Kindiki Kithure',
      party: 'Kenya Kwanza Coalition',
      initials: ['WR', 'KK'],
      avClass: ['av-green', 'av-green2'],
      accent: '#16a34a'
    },
    {
      id: 'sifuna-babu',
      names: 'Edwin Sifuna & Babu Owino',
      party: 'Orange Democratic Movement',
      initials: ['ES', 'BO'],
      avClass: ['av-blue', 'av-blue2'],
      accent: '#2563eb'
    },
    {
      id: 'gachagua-kalonzo',
      names: 'Rigathi Gachagua & Kalonzo Musyoka',
      party: 'United Democratic Alliance',
      initials: ['RG', 'SK'],
      avClass: ['av-orange', 'av-orange2'],
      accent: '#ea580c'
    }
  ];

  /* ─── Helpers ─────────────────────────────────────────────────── */
  function $(id) { return document.getElementById(id); }

  function api(path, method, body) {
    var opts = {
      method: method || 'GET',
      headers: { 'Content-Type': 'application/json' }
    };
    if (body) opts.body = JSON.stringify(body);
    return fetch(API_BASE + '/api' + path, opts).then(function (r) {
      return r.json().then(function (data) {
        if (!r.ok) throw new Error(data.detail || 'Request failed');
        return data;
      });
    });
  }

  function showToast(msg, duration) {
    var toast = $('toast');
    toast.textContent = msg;
    toast.classList.add('show');
    setTimeout(function () {
      toast.classList.remove('show');
    }, duration || 3000);
  }

  function setStep(n) {
    var panels = ['step-select', 'step-phone', 'step-pay', 'step-vote', 'step-success'];
    panels.forEach(function (id, i) {
      var el = $(id);
      if (el) el.classList.toggle('active', i === n);
    });
    for (var i = 0; i < 4; i++) {
      var dot = $('dot' + i);
      if (dot) {
        dot.classList.toggle('done', i < n);
        dot.classList.toggle('active', i === n);
      }
    }
    state.currentStep = n;
    window.scrollTo(0, 0);
  }

  function buildPreviewHTML(ticket) {
    return (
      '<div class="preview-avatar-group avatars">' +
        '<div class="avatar ' + ticket.avClass[0] + '" aria-hidden="true">' + ticket.initials[0] + '</div>' +
        '<div class="avatar ' + ticket.avClass[1] + '" aria-hidden="true">' + ticket.initials[1] + '</div>' +
      '</div>' +
      '<div>' +
        '<div class="preview-names">' + escHtml(ticket.names) + '</div>' +
        '<div class="preview-party">' + escHtml(ticket.party) + '</div>' +
      '</div>'
    );
  }

  function escHtml(str) {
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function maskPhone(phone) {
    // +254712345678  → +254 7XX XXX 678
    if (phone.length >= 10) {
      return phone.slice(0, 7) + 'XXX' + phone.slice(-3);
    }
    return phone;
  }

  /* ─── Step 1: Select Ticket ───────────────────────────────────── */
  window.selectTicket = function (idx) {
    state.selectedTicket = idx;
    var cards = document.querySelectorAll('.ticket-card');
    cards.forEach(function (card, i) {
      card.classList.toggle('selected', i === idx);
      card.setAttribute('aria-checked', i === idx ? 'true' : 'false');
    });
    $('btn-next-select').disabled = false;
    $('select-hint').textContent = 'Tap Continue to proceed';
  };

  window.keySelectTicket = function (event, idx) {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      selectTicket(idx);
    }
  };

  window.goToPhone = function () {
    if (state.selectedTicket === null) return;
    setStep(1);
    var preview = $('phone-ticket-preview');
    if (preview) preview.innerHTML = buildPreviewHTML(TICKETS[state.selectedTicket]);
  };

  /* ─── Step 2: Phone Entry ─────────────────────────────────────── */
  window.onPhoneInput = function () {
    var raw = $('phone-input').value.replace(/\D/g, '');
    var valid = /^[17]\d{8}$/.test(raw);
    $('btn-proceed-pay').disabled = !valid;
    var errEl = $('phone-error');
    if (raw.length >= 9 && !valid) {
      errEl.textContent = 'Enter a valid 9-digit Safaricom number (starts with 7 or 1)';
    } else {
      errEl.textContent = '';
    }
    if (valid) {
      state.phoneNumber = raw;
      state.normalizedPhone = '+254' + raw;
    }
  };

  window.goToPay = function () {
    var raw = $('phone-input').value.replace(/\D/g, '');
    if (!/^[17]\d{8}$/.test(raw)) return;

    state.phoneNumber = raw;
    state.normalizedPhone = '+254' + raw;

    // Register voter
    api('/start', 'POST', { phone_number: state.normalizedPhone })
      .then(function (data) {
        if (data.has_voted) {
          showToast('⚠️ This number has already voted.');
          return;
        }
        setStep(2);
        $('pay-phone-display').textContent = '+254 ' + raw;

        // If already paid (came back), skip to vote step
        if (data.payment_status === 'PAID') {
          simulatePaymentSuccess(null);  // no receipt needed, already in DB
        }
      })
      .catch(function (err) {
        if (err.message && err.message.includes('already voted')) {
          $('phone-error').textContent = 'This number has already voted.';
        } else {
          $('phone-error').textContent = 'Registration failed. Try again.';
        }
      });
  };

  window.goBack = function (toStep) {
    stopPolling();
    resetPayUI();
    setStep(toStep - 1);
  };

  /* ─── Step 3: Payment ─────────────────────────────────────────── */
  window.initiatePay = function () {
    $('stk-action').style.display = 'none';
    $('pay-waiting').removeAttribute('hidden');
    $('pay-error').setAttribute('hidden', '');

    api('/pay', 'POST', { phone_number: state.normalizedPhone })
      .then(function () {
        showToast('📱 STK push sent! Check your phone.');
        startPolling();
      })
      .catch(function (err) {
        resetPayUI();
        showPayError(err.message || 'M-Pesa request failed. Please try again.');
      });
  };

  window.resendSTK = function () {
    stopPolling();
    resetPayUI();
    setTimeout(function () { initiatePay(); }, 300);
  };

  window.retryPay = function () {
    $('pay-error').setAttribute('hidden', '');
    $('stk-action').style.display = 'block';
  };

  function showPayError(msg) {
    $('stk-action').style.display = 'block';
    $('pay-waiting').setAttribute('hidden', '');
    $('pay-error').removeAttribute('hidden');
    $('pay-error-msg').textContent = msg;
  }

  function resetPayUI() {
    $('stk-action').style.display = 'block';
    $('pay-waiting').setAttribute('hidden', '');
    $('pay-error').setAttribute('hidden', '');
  }

  /* ─── Payment Polling ─────────────────────────────────────────── */
  function startPolling() {
    var tries = 0;
    var maxTries = 24;  // 2 mins at 5s intervals

    function tick() {
      tries++;
      if (tries > maxTries) {
        stopPolling();
        showPayError('Payment timeout. Please try again or use a different number.');
        return;
      }

      // Countdown display
      var cd = $('countdown');
      var secs = 5;
      clearInterval(state.pollCountdown);
      state.pollCountdown = setInterval(function () {
        secs--;
        if (cd) cd.textContent = secs;
        if (secs <= 0) clearInterval(state.pollCountdown);
      }, 1000);

      api('/pay/status/' + encodeURIComponent(state.normalizedPhone))
        .then(function (data) {
          if (data.paid) {
            stopPolling();
            state.mpesaReceipt = data.mpesa_receipt;
            goToVoteStep(data.mpesa_receipt);
          }
          // else still pending — next poll in 5s
        })
        .catch(function () {
          // network error — keep polling silently
        });
    }

    // First check after 5s
    state.pollInterval = setInterval(tick, 5000);
  }

  function stopPolling() {
    if (state.pollInterval) {
      clearInterval(state.pollInterval);
      state.pollInterval = null;
    }
    if (state.pollCountdown) {
      clearInterval(state.pollCountdown);
      state.pollCountdown = null;
    }
  }

  // For dev/demo environments where M-Pesa isn't live
  window.simulatePaymentSuccess = function (receipt) {
    stopPolling();
    var r = receipt || ('QHJ' + Math.random().toString(36).substr(2, 6).toUpperCase());
    state.mpesaReceipt = r;
    goToVoteStep(r);
  };

  function goToVoteStep(receipt) {
    setStep(3);
    var t = TICKETS[state.selectedTicket];
    var preview = $('vote-ticket-preview');
    if (preview) preview.innerHTML = buildPreviewHTML(t);
    var rd = $('vote-receipt-display');
    if (rd && receipt) rd.textContent = 'Receipt: ' + receipt;
  }

  /* ─── Step 4: Cast Vote ───────────────────────────────────────── */
  window.castVote = function () {
    var btn = $('btn-cast-vote');
    btn.disabled = true;
    btn.innerHTML = '<div class="spinner" style="width:20px;height:20px;border-width:2px;margin:0"></div>';

    api('/vote', 'POST', {
      phone_number: state.normalizedPhone,
      candidate_id: TICKETS[state.selectedTicket].id
    })
      .then(function (data) {
        state.voteId = data.vote_id;
        setStep(4);
        fillReceipt(data);
      })
      .catch(function (err) {
        btn.disabled = false;
        btn.innerHTML = '🗳️ Cast My Vote';
        var errEl = $('vote-error');
        errEl.removeAttribute('hidden');
        $('vote-error-msg').textContent = err.message || 'Vote failed. Please try again.';
      });
  };

  function fillReceipt(data) {
    var t = TICKETS[state.selectedTicket];
    $('rc-candidate').textContent = t.names;
    $('rc-phone').textContent = maskPhone(state.normalizedPhone);
    $('rc-mpesa').textContent = data.mpesa_receipt || state.mpesaReceipt || '—';
    $('rc-vote-id').textContent = data.vote_id || '—';
    $('rc-time').textContent = new Date().toLocaleString('en-KE', {
      timeZone: 'Africa/Nairobi',
      year: 'numeric', month: 'short', day: 'numeric',
      hour: '2-digit', minute: '2-digit', second: '2-digit'
    });
  }

  /* ─── Share Receipt ───────────────────────────────────────────── */
  window.shareReceipt = function () {
    var t = TICKETS[state.selectedTicket];
    var text =
      '🗳️ I voted in the Kenya 2027 Presidential Election!\n' +
      'Candidate: ' + t.names + '\n' +
      'Vote ID: ' + (state.voteId || '—') + '\n' +
      'M-Pesa Receipt: ' + (state.mpesaReceipt || '—') + '\n' +
      'Cast via secure M-Pesa voting system.';

    if (navigator.share) {
      navigator.share({ title: 'My Kenya 2027 Vote', text: text })
        .catch(function () {});
    } else if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(function () {
        showToast('📋 Receipt copied to clipboard!');
      });
    } else {
      showToast('Share: ' + text.slice(0, 60) + '...');
    }
  };

  /* ─── PWA Service Worker ──────────────────────────────────────── */
  if ('serviceWorker' in navigator) {
    window.addEventListener('load', function () {
      navigator.serviceWorker.register('/static/sw.js').catch(function () {});
    });
  }

  /* ─── Prevent double-tap zoom on buttons (iOS) ────────────────── */
  document.addEventListener('touchend', function (e) {
    var tag = e.target.tagName;
    if (tag === 'BUTTON' || tag === 'INPUT') {
      e.preventDefault();
      e.target.click();
    }
  }, { passive: false });

})();