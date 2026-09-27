/*
  السلوكان الصغيران اللذان تكرّرا في كل شاشة: فتح الحوارات وإغلاقها، وإيقاف
  التركيز حيث يجب أن يبدأ القارئ.

  كانا يُكتبان داخل القوالب — نسخةٌ في سجل المشاركين وأخرى في سجل التسجيلات
  وثالثة في نموذج طلب الالتحاق — وثلاثة حرّاس تشترط ألّا يحمل قالبُ شاشةٍ
  سكربتاً، لأن سلوكاً يُنسخ في كل قالب يتباعد عن نفسه بلا أن ينتبه أحد: نسخة
  تُغلق قائمة `<details>` بعد الفتح وأخرى تنساها، ونسخة تستمع على `document`
  فتلحق الصفوف التي يستبدلها البحث الحيّ وأخرى تستمع على الأزرار فتموت معها.

  الاستماع على `document` لا على الأزرار: البحث الفوري يستبدل الجدول، وزرٌّ
  جديد بلا مستمع زرٌّ ميّت.
*/
(() => {
  "use strict";

  /** يملأ الحوار من `data-*` الزرّ الذي فُتح منه. */
  const fill = (dialog, data) => {
    dialog.querySelectorAll("[data-from]").forEach((node) => {
      const value = data[node.dataset.from] || "";
      // الحقل يأخذ قيمةً، وما عداه يأخذ نصّاً.
      if ("value" in node && node.tagName !== "A") node.value = value;
      else node.textContent = value;
    });
    // سطرٌ لا قيمة له لا يُعرض فارغاً: يختفي هو وعنوانه معاً.
    dialog.querySelectorAll("[data-key]").forEach((node) => {
      node.toggleAttribute("hidden", !data[node.dataset.key]);
    });
    dialog.querySelectorAll("[data-href]").forEach((node) => {
      const url = data[node.dataset.href] || "";
      node.toggleAttribute("hidden", !url);
      if (url) node.setAttribute("href", url);
    });
    const tone = dialog.querySelector("[data-tone]");
    if (tone) tone.className = "chip " + (data.latestTone || "");
  };

  document.addEventListener("click", (event) => {
    // صفُّ تفاصيل يُفتح في مكانه: الزرّ يسمّي صفَّه، والصفّ مخفيٌّ بـ`hidden`
    // لا بورقة أنماط — فما يخفيه الوسم يُظهره الزرّ حتى لو لم تُحمَّل الأنماط.
    const expander = event.target.closest("[data-expands]");
    if (expander) {
      const row = document.getElementById(expander.dataset.expands);
      if (!row) return;
      // خانة الاختيار تقول بنفسها ما إن كانت مفتوحة: الشرط هو تعليمها، لا
      // عدد النقرات عليها — فنقرةٌ ثانية تُلغي التعليم وتطوي ما كشفته.
      const open =
        expander.type === "checkbox" ? expander.checked : row.hasAttribute("hidden");
      row.toggleAttribute("hidden", !open);
      expander.setAttribute("aria-expanded", open ? "true" : "false");
      return;
    }
    const closer = event.target.closest("[data-closes]");
    if (closer) {
      closer.closest("dialog")?.close();
      return;
    }
    const opener = event.target.closest("[data-opens]");
    if (!opener) return;
    const dialog = document.getElementById(opener.dataset.opens);
    if (!dialog || typeof dialog.showModal !== "function") return;
    // القائمة التي فُتح منها تُطوى، وإلا بقيت مفتوحة خلف الحوار.
    opener.closest("details")?.removeAttribute("open");
    fill(dialog, opener.dataset);
    dialog.showModal();
    focusInside(dialog);
    dialog.addEventListener("close", () => opener.focus(), { once: true });
  });

  /*
    الحقل الذي يبدأ عنده العمل داخل الحوار.

    حوارٌ يُفتح ثم يُطلب من القارئ أن ينقر حقله الأول هو نقرةٌ زائدة في كل مرّة،
    وللوحة الانتقال السريع خاصّةً: تُفتح بضغط «/» فالأصبع على المفتاح سلفاً.
    ويُكتب هنا لا في القالب، لأن §7 يمنع `<script>` في قوالب الشاشات — ولأن
    حوارَين يحتاجانه غداً سيكتبانه نسختين تختلفان.
  */
  function focusInside(dialog) {
    const start = dialog.querySelector("[data-dialog-autofocus]");
    if (start) requestAnimationFrame(() => start.focus());
  }

  /*
    قائمةٌ طويلة يُبحث فيها بدل التمرير.

    `<select>` بمئة خيار أداةُ تمرير لا أداةُ اختيار: من يعرف اسم المشارك
    يقرأ مئة سطر ليجده. الصندوق حلّ هذا ببحثٍ قبل الاختيار، وهذا هو نفسه
    لقوائم النماذج: خانة بحث تُدرَج فوق القائمة فتُخفي ما لا يطابق.

    تحسينٌ تدريجي: القائمة تعمل كاملةً بلا سكربت، وما يضيفه هذا تصفيةٌ
    فوقها. ولا يُدرَج إلا من ثمانية خيارات فصاعداً — خانة بحثٍ فوق ثلاثة
    خيارات ضجيجٌ لا خدمة.
  */
  const SEARCH_FROM = 8;
  document.querySelectorAll("select[data-searchable]").forEach((select) => {
    const options = Array.from(select.options);
    if (options.length < SEARCH_FROM) return;

    const box = document.createElement("input");
    box.type = "search";
    box.className = "sel-search";
    box.placeholder = select.dataset.searchable || "ابحث…";
    box.autocomplete = "off";
    box.setAttribute("aria-controls", select.id);
    const label = document.querySelector('label[for="' + select.id + '"]');
    box.setAttribute("aria-label", "تصفية " + (label ? label.textContent.trim() : ""));
    select.parentNode.insertBefore(box, select);

    box.addEventListener("input", () => {
      const needle = box.value.trim().toLowerCase();
      let shown = 0;
      options.forEach((option) => {
        const hit = !needle || option.textContent.toLowerCase().includes(needle);
        option.hidden = !hit;
        if (hit) shown += 1;
      });
      // قائمةٌ اختيارها مخفيٌّ تكذب على قارئها: تنتقل إلى أول ما بقي ظاهراً.
      if (shown && select.selectedOptions[0] && select.selectedOptions[0].hidden) {
        const first = options.find((option) => !option.hidden);
        if (first) select.value = first.value;
      }
    });
  });

  // بعد رفضٍ، القارئ يعود إلى أعلى الصفحة: التركيز يقف على ما يشرح الرفض
  // ليُقرأ، لا على حقل يُخمَّن.
  const start = document.querySelector("[data-autofocus]");
  if (start) start.focus({ preventScroll: false });
})();
