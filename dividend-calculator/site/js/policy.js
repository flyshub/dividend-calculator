/*
 * 估值档位阈值（页面唯一来源，issue #151）
 *
 * 数字来自 Python 的 src/pr_calculator.py（PR_UNDERVALUED_MAX / PR_FAIR_LOW_MAX /
 * PR_FAIR_MAX）。页面不写死任何阈值：
 *   - 文案段落用 <span data-policy="..."> 占位，由本文件在 DOMContentLoaded 时填充
 *   - 脚本拼装的说明文字用 PRPolicy.zoneLegend()
 *
 * scripts/verify_js_vs_python.py 会解析下面的 PR_ZONE_CUTOFFS 与 Python 比对，
 * 双端不一致即 CI 失败（tests/test_policy_parity.py 亦直接校验）。
 */
(function (global) {
  'use strict';

  var PR_ZONE_CUTOFFS = { undervalued: 0.5, fair_low: 1.0, fair_max: 3.0 };

  /* 0.5 → "0.5"；1.0 → "1.0"（与页面历史文案一致的格式） */
  function fmtOne(v) { return Number(v).toFixed(1); }
  /* 1.0 → "1"（历史文案里 "PR≤1" 用整数写法） */
  function fmtInt(v) { return String(Number(v)); }

  function pick(format, value) {
    if (format === 'one-decimal') return fmtOne(value);
    if (format === 'int') return fmtInt(value);
    return String(value);
  }

  /* 「市赚率≤0.5低估，0.5-1.0合理偏低，1.0-3.0合理，>3.0高估」 */
  function zoneLegend() {
    var u = PR_ZONE_CUTOFFS.undervalued;
    var f = PR_ZONE_CUTOFFS.fair_low;
    var m = PR_ZONE_CUTOFFS.fair_max;
    return '市赚率≤' + fmtOne(u) + '低估，' + fmtOne(u) + '-' + fmtOne(f) + '合理偏低，'
      + fmtOne(f) + '-' + fmtOne(m) + '合理，>' + fmtOne(m) + '高估';
  }

  /* 填充 <span data-policy="undervalued|fair_low|fair_max" [data-format]> */
  function fillPolicySpans(root) {
    var nodes = (root || document).querySelectorAll('[data-policy]');
    for (var i = 0; i < nodes.length; i++) {
      var key = nodes[i].getAttribute('data-policy');
      if (!Object.prototype.hasOwnProperty.call(PR_ZONE_CUTOFFS, key)) continue;
      nodes[i].textContent = pick(nodes[i].getAttribute('data-format'),
        PR_ZONE_CUTOFFS[key]);
    }
  }

  if (typeof document !== 'undefined') {
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', function () { fillPolicySpans(); });
    } else {
      fillPolicySpans();
    }
  }

  global.PRPolicy = {
    cutoffs: PR_ZONE_CUTOFFS,
    zoneLegend: zoneLegend,
    fillSpans: fillPolicySpans,
  };
}(typeof self !== 'undefined' ? self : this));
