(function () {
    const categoryNames = {
        desktop: 'id_feature_categories_0',
        mobile: 'id_feature_categories_1',
        staff_portal: 'id_feature_categories_2',
    };
    const featureGroups = {
        desktop: 'id_desktop_features',
        mobile: 'id_mobile_features',
        staff_portal: 'id_staff_portal_features',
    };

    function setGroupState(group, enabled) {
        const groupElement = document.getElementById(group);
        if (!groupElement) return;
        const row = groupElement.closest('.form-row');
        if (row) row.style.display = enabled ? '' : 'none';
        const inputs = groupElement.querySelectorAll('input[type="checkbox"]');
        if (enabled && !Array.from(inputs).some((input) => input.checked)) {
            inputs.forEach((input) => { input.checked = true; });
        }
        inputs.forEach((input) => { input.disabled = !enabled; });
    }

    function syncCategories() {
        const desktopCategory = document.getElementById(categoryNames.desktop);
        if (desktopCategory) {
            desktopCategory.checked = true;
            desktopCategory.disabled = true;
        }
        setGroupState(featureGroups.desktop, true);
        setGroupState(featureGroups.mobile, document.getElementById(categoryNames.mobile)?.checked);
        setGroupState(featureGroups.staff_portal, document.getElementById(categoryNames.staff_portal)?.checked);
    }

    document.addEventListener('DOMContentLoaded', function () {
        document.querySelectorAll('#id_feature_categories input').forEach((input) => {
            input.addEventListener('change', syncCategories);
        });
        syncCategories();
    });
})();