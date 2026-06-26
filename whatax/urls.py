"""URL routing. Mounted under ^whatax/ by auth_hooks."""

from django.urls import path

from whatax import views

app_name = "whatax"

urlpatterns = [
    # Dashboard (user)
    path("", views.index, name="index"),
    # Structures (read-only drill pop schedule & warnings)
    path("structures/", views.structures, name="structures"),
    # Characters (any whatax role): allowed roster + KOS list
    path("characters/", views.characters, name="characters"),
    # Staff
    path("staff/", views.staff, name="staff"),
    path("staff/outstanding/", views.staff_outstanding, name="staff_outstanding"),
    path("staff/payments/", views.staff_payments, name="staff_payments"),
    path(
        "staff/mining/<int:year>/<int:month>/",
        views.staff_mining_month,
        name="staff_mining_month",
    ),
    path(
        "staff/structure/<int:structure_id>/dismiss-pop/",
        views.structure_pop_dismiss,
        name="structure_pop_dismiss",
    ),
    path("period/<int:period_id>/", views.period_detail, name="period"),
    path(
        "period/<int:period_id>/player/<int:user_id>/",
        views.period_player_detail,
        name="period_player",
    ),
    path(
        "period/<int:period_id>/unregistered/<int:character_id>/",
        views.period_unregistered_detail,
        name="period_unregistered",
    ),
    path("payment/<int:payment_id>/match/", views.payment_match, name="payment_match"),
    path("record/<int:record_id>/add-payment/", views.record_add_payment, name="record_add_payment"),
    path("record/<int:record_id>/adjust/", views.record_adjust, name="record_adjust"),
    path("record/<int:record_id>/edit-tax/", views.record_edit_tax, name="record_edit_tax"),
    # Admin
    path("record/<int:record_id>/waive/", views.record_waive, name="record_waive"),
    path("admin/", views.admin_config, name="admin"),
    path("admin/clear-all-debts/", views.clear_all_debts, name="clear_all_debts"),
    path("admin/run-calc/", views.run_calc, name="run_calc"),
    path("admin/period-delete/", views.period_delete, name="period_delete"),
    path("admin/corp-rate/<int:rate_id>/delete/", views.corp_rate_delete, name="corp_rate_delete"),
    path("admin/good-ores/", views.admin_good_ores, name="admin_good_ores"),
    path(
        "admin/good-ores/default/<int:default_id>/delete/",
        views.good_ore_default_delete,
        name="good_ore_default_delete",
    ),
    path(
        "admin/good-ores/override/<int:override_id>/delete/",
        views.structure_good_ore_delete,
        name="structure_good_ore_delete",
    ),
    path("admin/kos/", views.admin_kos, name="admin_kos"),
    path("admin/kos/<int:kos_id>/delete/", views.kos_delete, name="kos_delete"),
    path("admin/groups/", views.admin_groups, name="admin_groups"),
    path("admin/groups/<int:group_id>/delete/", views.moon_group_delete, name="moon_group_delete"),
    path(
        "admin/groups/structure/<int:structure_id>/remove/",
        views.moon_group_remove_structure,
        name="moon_group_remove_structure",
    ),
    path("admin/token/structures/", views.add_structures_token, name="add_structures_token"),
    path("admin/token/wallet/", views.add_wallet_token, name="add_wallet_token"),
    path("admin/token/<int:token_id>/remove/", views.remove_token, name="remove_token"),
]
