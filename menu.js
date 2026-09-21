/* ==========================================================================
   menu.js - 共通メニュー用スクリプト (修正版)
   ========================================================================== */

document.addEventListener('DOMContentLoaded', () => {
    // 要素の取得
    const pcSubmenuBtn = document.getElementById('pcSubmenuBtn');
    const pcSubmenu = document.getElementById('pcSubmenu');
    
    const spSubBtn = document.getElementById('spSubBtn');
    const spBottomSheet = document.getElementById('spBottomSheet');
    const menuOverlay = document.getElementById('menuOverlay');

    const spFooter = document.getElementById('spFooter');



    // --- 1. PCサブメニュー（アコーディオン）の制御 ---
    if (pcSubmenuBtn && pcSubmenu) {
        pcSubmenuBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            pcSubmenu.classList.toggle('open');
        });
    }

    // --- 2. スマホ用ボトムシートの制御 ---
    if (spSubBtn && spBottomSheet && menuOverlay) {
        spSubBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            spBottomSheet.classList.add('open');
            menuOverlay.classList.add('active');
        });
    }

    // オーバーレイまたはボトムシート内のリンククリックで閉じる
    if (menuOverlay) {
        menuOverlay.addEventListener('click', closeAllMenus);
    }

    if (spBottomSheet) {
        const sheetLinks = spBottomSheet.querySelectorAll('a');
        sheetLinks.forEach(link => {
            link.addEventListener('click', closeAllMenus);
        });
    }

    function closeAllMenus() {
        if (spBottomSheet) spBottomSheet.classList.remove('open');
        if (menuOverlay) menuOverlay.classList.remove('active');
    }



    // --- 3. スマホ用：上下スクロール連動非表示制御 ---
    let lastScrollY = window.scrollY;
    
    if (spFooter) {
        window.addEventListener('scroll', () => {
            const currentScrollY = window.scrollY;

            if (currentScrollY > lastScrollY && currentScrollY > 60) {
                spFooter.classList.add('hide');
            } else {
                spFooter.classList.remove('hide');
            }

            lastScrollY = currentScrollY;
        });
    }
});