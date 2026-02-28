<script setup>
import { ref, onMounted, watch } from 'vue'
import { Chart, registerables } from 'chart.js'

Chart.register(...registerables)

const props = defineProps({
  points: Array,
})

const canvas = ref(null)
let chart = null

function buildChart() {
  if (!canvas.value || !props.points?.length) return

  const labels  = props.points.map(p => p.time)
  const solar   = props.points.map(p => p.solar_w)
  const imp     = props.points.map(p => p.grid_import_w)
  const exp     = props.points.map(p => p.grid_export_w)
  const impSim  = props.points.map(p => p.grid_import_sim_w)
  const expSim  = props.points.map(p => p.grid_export_sim_w)
  const hasGrid = imp.some(v => v !== null)
  const hasSim  = impSim.some(v => v !== null)

  const datasets = [
    {
      label: 'Solar',
      data: solar,
      borderColor: '#F59E0B',
      backgroundColor: 'rgba(245,158,11,0.15)',
      fill: true,
      tension: 0.3,
      pointRadius: 0,
      borderWidth: 2,
    },
  ]

  if (hasGrid) {
    datasets.push({
      label: 'Grid import',
      data: imp,
      borderColor: '#93C5FD',
      backgroundColor: 'transparent',
      tension: 0.3,
      pointRadius: 0,
      borderWidth: 1,
    })
    datasets.push({
      label: 'Grid export',
      data: exp,
      borderColor: '#6EE7B7',
      backgroundColor: 'transparent',
      tension: 0.3,
      pointRadius: 0,
      borderWidth: 1,
    })
  }

  if (hasSim) {
    datasets.push({
      label: 'Import (8kWh 🔋)',
      data: impSim,
      borderColor: '#60A5FA',
      backgroundColor: 'transparent',
      tension: 0.3,
      pointRadius: 0,
      borderWidth: 2,
      borderDash: [4, 3],
    })
    datasets.push({
      label: 'Export (8kWh 🔋)',
      data: expSim,
      borderColor: '#34D399',
      backgroundColor: 'transparent',
      tension: 0.3,
      pointRadius: 0,
      borderWidth: 2,
      borderDash: [4, 3],
    })
  }

  if (chart) chart.destroy()

  chart = new Chart(canvas.value, {
    type: 'line',
    data: { labels, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: 'index', intersect: false },
      scales: {
        x: {
          ticks: {
            color: '#6B7280',
            maxTicksLimit: 12,
            font: { size: 11 },
          },
          grid: { color: 'rgba(255,255,255,0.04)' },
        },
        y: {
          ticks: {
            color: '#6B7280',
            font: { size: 11 },
            callback: v => v >= 1000 ? (v/1000).toFixed(1)+'k' : v,
          },
          grid: { color: 'rgba(255,255,255,0.04)' },
          min: 0,
        },
      },
      plugins: {
        legend: {
          labels: { color: '#9CA3AF', boxWidth: 12, font: { size: 11 } },
        },
        tooltip: {
          callbacks: {
            label: ctx => {
              const v = ctx.parsed.y
              if (v === null) return null
              return ` ${ctx.dataset.label}: ${v >= 1000 ? (v/1000).toFixed(2)+' kW' : Math.round(v)+' W'}`
            },
          },
        },
      },
    },
  })
}

onMounted(buildChart)
watch(() => props.points, buildChart, { deep: true })
</script>

<template>
  <div class="bg-gray-900 rounded-2xl p-5 flex flex-col gap-3 flex-1 min-h-0">
    <h2 class="flex-shrink-0 text-xs font-medium text-gray-500 uppercase tracking-widest">Today's production</h2>
    <div class="relative flex-1 min-h-0" style="min-height: 12rem">
      <canvas ref="canvas" class="absolute inset-0 w-full h-full" />
    </div>
  </div>
</template>
