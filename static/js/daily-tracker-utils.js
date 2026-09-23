(function () {
    function escapeHtml(value) {
        return String(value ?? "").replace(/[&<>"']/g, (character) => ({
            "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
        }[character]));
    }

    function escapeCsvCell(value) {
        let cell = String(value ?? "");
        // Evita que Excel interprete contenido introducido por usuarios como fórmula.
        if (/^[=+\-@]/.test(cell)) cell = `'${cell}`;
        return `"${cell.replace(/"/g, '""')}"`;
    }

    window.DailyTrackerUI = Object.freeze({ escapeHtml, escapeCsvCell });
}());
