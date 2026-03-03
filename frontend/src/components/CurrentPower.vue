<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import SolarOpportunity from './SolarOpportunity.vue'

defineProps({
  data:     Object,
  forecast: Array,
})

const dots = ref(0)
let dotsTimer
onMounted(() => { dotsTimer = setInterval(() => { dots.value = (dots.value + 1) % 6 }, 500) })
onUnmounted(() => clearInterval(dotsTimer))

function fmt(w) {
  if (w === null || w === undefined) return '—'
  if (w >= 1000) return (w / 1000).toFixed(2) + ' kW'
  return Math.round(w) + ' W'
}

function socColor(pct) {
  if (pct === null || pct === undefined) return 'text-gray-600'
  if (pct >= 70) return 'text-emerald-400'
  if (pct >= 30) return 'text-amber-400'
  return 'text-blue-400'
}

function carbonLabel(g) {
  if (g === null || g === undefined) return null
  if (g < 100) return { text: Math.round(g) + ' g · Clean', cls: 'bg-emerald-900 text-emerald-300 ring-1 ring-emerald-700' }
  if (g < 250) return { text: Math.round(g) + ' g · Mixed', cls: 'bg-amber-900 text-amber-300 ring-1 ring-amber-700' }
  return { text: Math.round(g) + ' g · Dirty', cls: 'bg-orange-900 text-orange-300 ring-1 ring-orange-700' }
}
</script>

<template>
  <div class="flex flex-col gap-3">
    <SolarOpportunity :data="data" :forecast="forecast" />
    <div class="flex items-center justify-between bg-gray-900 rounded-2xl px-4 py-3">
      <div class="flex flex-col items-center gap-0.5">
        <span class="text-xs text-gray-500 uppercase tracking-widest">Solar</span>
        <span class="text-2xl font-bold text-amber-400">{{ fmt(data?.solar_w) }}</span>
      </div>
      <div class="w-px h-8 bg-gray-800" />
      <div class="flex flex-col items-center gap-0.5">
        <span class="text-xs text-gray-500 uppercase tracking-widest">Consuming</span>
        <span class="text-2xl font-bold" :class="data?.consumption_stale ? 'text-gray-500' : 'text-white'"
              style="min-width: 3.5ch; display: inline-block; text-align: center">
          {{ data?.consumption_stale ? ('·'.repeat(dots) || '\xa0') : fmt(data?.consumption_w) }}
        </span>
      </div>
      <div class="w-px h-8 bg-gray-800" />
      <div class="flex flex-col items-center gap-0.5">
        <span class="text-xs text-gray-500 uppercase tracking-widest">Grid</span>
        <template v-if="data?.grid_import_w !== null && data?.grid_import_w !== undefined">
          <span v-if="data.grid_export_w > data.grid_import_w" class="text-2xl font-bold text-emerald-400">{{ fmt(data.grid_export_w) }}</span>
          <span v-else class="text-2xl font-bold text-blue-400">{{ fmt(data.grid_import_w) }}</span>
          <span
            v-if="data.grid_import_w >= data.grid_export_w && carbonLabel(data.carbon_g_per_kwh)"
            :class="['text-xs px-1.5 py-0.5 rounded-full font-medium', carbonLabel(data.carbon_g_per_kwh).cls]"
          >{{ carbonLabel(data.carbon_g_per_kwh).text }}</span>
        </template>
        <span v-else class="text-2xl font-bold text-gray-600">—</span>
      </div>
      <template v-if="data?.battery_kwh">
        <div class="w-px h-8 bg-gray-800" />
        <div class="flex flex-col items-center gap-0.5">
          <span class="text-xs text-gray-500 uppercase tracking-widest">Battery</span>
          <span class="text-2xl font-bold" :class="socColor(data.battery_soc_pct)">
            {{ data.battery_soc_pct !== null ? data.battery_soc_pct + '%' : '—' }}
          </span>
        </div>
      </template>
    </div>
  </div>
</template>
