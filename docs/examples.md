[Setup](setup.md) · [Entities](entities.md) · [Dynamic charging location](dynamic.md) · [Examples](examples.md) · [Development](development.md)

# Examples
Entity ids follow the names you chose; replace `car` and `willem` below with your own. Find them
under Settings → Devices & services → PublicChargeWatch.

**Map card**: the followed entity and the charger it is heading for:
```yaml
type: map
entities:
  - entity: person.willem
  - entity: device_tracker.car_nearest_charger   # the nearest-charger tracker
hours_to_show: 0
```

**Entities card** with the details of the best charger:
```yaml
type: entities
entities:
  - sensor.car_nearest_charger            # address
  - sensor.car_distance
  - sensor.car_free_charge_points         # attribute free_of_total, e.g. 1/3
  - sensor.car_power
  - sensor.car_plug_type
```

**Markdown card** listing the top 3 (from the `results` attribute):
```yaml
type: markdown
content: >-
  {% for r in state_attr('sensor.car_nearest_charger', 'results') %}
  **{{ r.address }}**: {{ r.distance_m }} m, {{ r.free }}/{{ r.total }} free,
  {{ r.power_kw }} kW, {{ r.price_per_kwh | default('?', true) }} €/kWh
  [Navigate]({{ r.navigate_url }})
  {% endfor %}
```

**Automation**: when a better charger with a free charge point becomes the nearest, notify the
phone with a button that starts navigation. Only while "I need to charge" is on:
```yaml
alias: Tell me where I can charge
triggers:
  - trigger: state
    entity_id: event.car_nearest_charger
conditions:
  - condition: state
    entity_id: input_boolean.need_charge
    state: "on"
  - condition: template   # ignore the entity going unavailable / restoring its state
    value_template: "{{ trigger.to_state.attributes.event_type == 'nearest_changed' }}"
actions:
  - action: notify.mobile_app_my_phone
    data:
      title: "Charger available"
      message: >-
        {{ trigger.to_state.attributes.address }}, {{ trigger.to_state.attributes.distance_m }} m,
        {{ trigger.to_state.attributes.free }}/{{ trigger.to_state.attributes.total }} free,
        {{ trigger.to_state.attributes.power_kw }} kW
      data:
        actions:
          - action: URI
            title: Navigate
            uri: "{{ trigger.to_state.attributes.navigate_url }}"
```
The event's attributes are the same as the nearest-charger sensor's (`address`, `distance_m`, `free`,
`total`, `power_kw`, `price_per_kwh`, `connectors`, `operator`, `navigate_url`, `last_updated`).

**Automation for a fixed location**: the *Availability* event of a charging location you added
(`became_available` / `became_occupied`):
```yaml
triggers:
  - trigger: state
    entity_id: event.jacob_cnodestraat_23_availability
conditions:
  - condition: template
    value_template: "{{ trigger.to_state.attributes.event_type == 'became_available' }}"
actions:
  - action: notify.mobile_app_my_phone
    data:
      message: "{{ trigger.to_state.attributes.address }}: {{ trigger.to_state.attributes.free }} free"
```

**Template sensor**: is anything nearby free and cheap?
```yaml
template:
  - binary_sensor:
      - name: Cheap charger nearby
        state: >-
          {% set p = state_attr('sensor.car_nearest_charger', 'price_per_kwh') %}
          {{ p is not none and p < 0.40 and states('sensor.car_free_charge_points') | int(0) > 0 }}
```
