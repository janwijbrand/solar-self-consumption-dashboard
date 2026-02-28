<script setup>
defineProps({
  data: Object,
  title: { type: String, default: 'Today' },
})

function kwh(val) {
  if (val === null || val === undefined) return '—'
  return val.toFixed(2) + ' kWh'
}
</script>

<template>
  <div class="bg-gray-900 rounded-2xl p-5 space-y-4">
    <h2 class="text-xs font-medium text-gray-500 uppercase tracking-widest">{{ title }}</h2>

    <div class="grid grid-cols-2 gap-x-8 gap-y-3 text-sm">
      <!-- Solar produced -->
      <div>
        <div class="text-gray-500 text-xs mb-0.5">Solar produced</div>
        <div class="text-amber-400 font-semibold text-lg">{{ kwh(data?.solar_kwh) }}</div>
      </div>

      <!-- Total consumed -->
      <div>
        <div class="text-gray-500 text-xs mb-0.5">Total consumed</div>
        <div class="text-white font-semibold text-lg">{{ kwh(data?.total_consumed_kwh) }}</div>
      </div>

      <!-- Self-consumed -->
      <div>
        <div class="text-gray-500 text-xs mb-0.5">Self-consumed</div>
        <div class="text-emerald-400 font-semibold text-lg">{{ kwh(data?.self_consumed_kwh) }}</div>
      </div>

      <!-- From grid -->
      <div>
        <div class="text-gray-500 text-xs mb-0.5">From grid</div>
        <div class="text-blue-400 font-semibold text-lg">{{ kwh(data?.grid_import_kwh) }}</div>
      </div>

      <!-- Exported -->
      <div>
        <div class="text-gray-500 text-xs mb-0.5">Exported to grid</div>
        <div class="text-gray-300 font-semibold text-lg">{{ kwh(data?.grid_export_kwh) }}</div>
      </div>
    </div>

    <!-- Progress bars -->
    <div v-if="data?.solar_kwh > 0" class="space-y-3 pt-1">
      <!-- Self-consumption pair -->
      <div class="space-y-1">
        <div class="flex justify-between text-xs text-gray-500">
          <span>Solar self-consumed</span>
          <div class="flex items-center gap-1.5">
            <span class="text-amber-400 font-medium">{{ data.self_consumption_pct }}%</span>
            <template v-if="data?.battery_kwh">
              <span class="text-gray-700">→</span>
              <span class="text-amber-400/60 font-medium">{{ data.self_consumption_sim_pct }}% 🔋</span>
            </template>
          </div>
        </div>
        <div class="h-2 bg-gray-800 rounded-full overflow-hidden">
          <div class="h-full bg-amber-400 rounded-full transition-all duration-500"
               :style="{ width: data.self_consumption_pct + '%' }" />
        </div>
        <div v-if="data?.battery_kwh" class="h-2 bg-gray-800 rounded-full overflow-hidden">
          <div class="h-full bg-amber-400/50 rounded-full transition-all duration-500"
               :style="{ width: data.self_consumption_sim_pct + '%' }" />
        </div>
      </div>

      <!-- Solar fraction pair -->
      <div class="space-y-1">
        <div class="flex justify-between text-xs text-gray-500">
          <span>Consumption from solar</span>
          <div class="flex items-center gap-1.5">
            <span class="text-emerald-400 font-medium">{{ data.solar_fraction_pct }}%</span>
            <template v-if="data?.battery_kwh">
              <span class="text-gray-700">→</span>
              <span class="text-emerald-400/60 font-medium">{{ data.solar_fraction_sim_pct }}% 🔋</span>
            </template>
          </div>
        </div>
        <div class="h-2 bg-gray-800 rounded-full overflow-hidden">
          <div class="h-full bg-emerald-400 rounded-full transition-all duration-500"
               :style="{ width: data.solar_fraction_pct + '%' }" />
        </div>
        <div v-if="data?.battery_kwh" class="h-2 bg-gray-800 rounded-full overflow-hidden">
          <div class="h-full bg-emerald-400/50 rounded-full transition-all duration-500"
               :style="{ width: data.solar_fraction_sim_pct + '%' }" />
        </div>
      </div>

      <!-- Grid import saved -->
      <div v-if="data?.battery_kwh" class="flex justify-between text-xs text-gray-500">
        <span>Grid import saved 🔋</span>
        <span class="text-blue-400/70 font-medium">{{ kwh(data.grid_import_saved_kwh) }}</span>
      </div>
    </div>
  </div>
</template>
