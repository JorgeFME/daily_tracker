import re

content = open('templates/index.html', encoding='utf-8').read()

# Find and replace the fetchWeeklyHours function
pattern = r'async function fetchWeeklyHours\(\) \{.*?\n        \}'
match = re.search(pattern, content, re.DOTALL)
if not match:
    print("NOT FOUND")
    exit(1)

print(f"Found at {match.start()}-{match.end()}")
print("Preview:", repr(match.group()[:200]))

new_func = '''async function fetchWeeklyHours() {
            const uid = userSelect.getValue(), date = fp.input.value;
            if (!uid || !date) return;

            // Limpiar banner de ausencia previo
            let bannerEl = document.getElementById('ausencia-banner');
            if (bannerEl) bannerEl.remove();

            try {
                const [resW, resD] = await Promise.all([
                    fetch(`/api/weekly_hours?user=${uid}&date=${date}`),
                    fetch(`/api/daily_hours?user=${uid}&date=${date}`)
                ]);
                const dataW = await resW.json();
                const dataD = await resD.json();
                horasEnDB = dataW.total || 0;
                horasDiarias = parseFloat(dataD.total) || 0;
                const horasAusencia = parseFloat(dataD.horas_ausencia) || 0;

                const fechaLabel = fp.altInput ? fp.altInput.value : date;
                const labelEl = document.getElementById('semana-label');
                if (labelEl) labelEl.textContent = fechaLabel;

                const maxHoras = Math.max(0, 8 - horasAusencia);
                const disponibles = Math.max(0, maxHoras - horasDiarias);
                hoursInput.max = disponibles;

                if (horasAusencia >= 8) {
                    const banner = document.createElement('div');
                    banner.id = 'ausencia-banner';
                    banner.style.cssText = 'background:#fef9c3;border:1px solid #fde047;border-radius:10px;padding:10px 14px;margin-bottom:12px;font-size:0.85rem;color:#854d0e;display:flex;align-items:center;gap:8px;';
                    banner.innerHTML = '<i class="bi bi-calendar-x-fill" style="font-size:1.1rem"></i><span><strong>Ausencia registrada.</strong> Este d\u00eda no contabiliza horas laborables.</span>';
                    const wrap = hoursInput.closest('form');
                    if (wrap) wrap.prepend(banner);
                    hoursInput.value = '';
                    hoursInput.disabled = true;
                    hoursInput.placeholder = 'D\u00eda de ausencia';
                } else if (horasAusencia > 0) {
                    const banner = document.createElement('div');
                    banner.id = 'ausencia-banner';
                    banner.style.cssText = 'background:#fff7ed;border:1px solid #fdba74;border-radius:10px;padding:10px 14px;margin-bottom:12px;font-size:0.85rem;color:#9a3412;display:flex;align-items:center;gap:8px;';
                    banner.innerHTML = `<i class="bi bi-clock-history" style="font-size:1.1rem"></i><span><strong>Ausencia parcial (${horasAusencia}h).</strong> M\u00e1ximo registrable hoy: ${maxHoras}h.</span>`;
                    const wrap = hoursInput.closest('form');
                    if (wrap) wrap.prepend(banner);
                    if (projectSelect.getValue()) hoursInput.disabled = false;
                    hoursInput.placeholder = `M\u00e1x. ${disponibles}h`;
                    if (parseFloat(hoursInput.value) > disponibles) hoursInput.value = disponibles;
                } else if (disponibles === 0) {
                    hoursInput.value = '';
                    hoursInput.disabled = true;
                    hoursInput.placeholder = 'L\u00edmite alcanzado';
                } else {
                    if (projectSelect.getValue()) hoursInput.disabled = false;
                    hoursInput.placeholder = `M\u00e1x. ${disponibles}h`;
                    if (parseFloat(hoursInput.value) > disponibles) hoursInput.value = disponibles;
                }

                updateWeeklyProgress(hoursInput.value);
                validateForm();
            } catch (e) { console.error(e); }
        }'''

content = content[:match.start()] + new_func + content[match.end():]
open('templates/index.html', 'w', encoding='utf-8').write(content)
print("Done!")
