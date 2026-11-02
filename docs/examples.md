[Setup](setup.md) · [Entities](entities.md) · [Dynamic charging location](dynamic.md) · [Examples](examples.md) · [Development](development.md)

# Examples
Entity ids are the device name plus the entity name, so they depend on the name you gave the
dynamic location (default "Nearest charger") and on your Home Assistant language: with Dutch
names the sensor is `sensor.nearest_charger_dichtstbijzijnde_laadpaal`. The examples use the English
defaults; copy your own ids from Settings → Devices & services → PublicChargeWatch.

**Map card**: the followed entity and the charger it is heading for:
```yaml
type: map
entities:
  - entity: person.willem
  - entity: device_tracker.nearest_charger_nearest_charger   # the nearest-charger tracker
hours_to_show: 0
```

**Entities card** with the details of the best charger:
```yaml
type: entities
entities:
  - sensor.nearest_charger_nearest_charger            # address
  - sensor.nearest_charger_distance
  - sensor.nearest_charger_free_charge_points         # attribute free_of_total, e.g. 1/3
  - sensor.nearest_charger_power
  - sensor.nearest_charger_plug_type
```

**Markdown card** listing the top 3 (from the `results` attribute). In the card editor paste the
content below **without any leading spaces** (the editor keeps them and the list turns into
nested bullets):
```
{% for r in state_attr('sensor.nearest_charger_nearest_charger', 'results') or [] %}
- **{{ r.address }}**: {{ r.distance_m }} m, {{ r.free }}/{{ r.total }} free, {{ r.power_kw }} kW{% if r.price_per_kwh is not none %}, €{{ r.price_per_kwh }}/kWh{% endif %} · [Navigate]({{ r.navigate_url }})
{%- endfor %}
```
In YAML mode the same text goes under `content: |`, indented as a block:
```yaml
type: markdown
content: |
  {% for r in state_attr('sensor.nearest_charger_nearest_charger', 'results') or [] %}
  - **{{ r.address }}**: {{ r.distance_m }} m, {{ r.free }}/{{ r.total }} free, {{ r.power_kw }} kW
  {%- endfor %}
```
Use `|`, not `>-`: the line breaks are what make it a list.

**Automation**: when a better charger with a free charge point becomes the nearest, notify the
phone with a button that starts navigation. Only while "I need to charge" is on:
```yaml
alias: Tell me where I can charge
triggers:
  - trigger: state
    entity_id: event.nearest_charger_nearest_charger
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
          {% set p = state_attr('sensor.nearest_charger_nearest_charger', 'price_per_kwh') %}
          {{ p is not none and p < 0.40 and states('sensor.nearest_charger_free_charge_points') | int(0) > 0 }}
```
