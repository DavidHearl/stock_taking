from django.db import migrations


WAITING_NAME = 'Allow time for orders to arrive'
MANUFACTURE_NAME = 'Manufacture Product'
MERGED_DESCRIPTION = (
	'Allow time for the ordered goods to arrive, then manufacture and prepare '
	'all components for installation. Organise delivery once all products are '
	'completed.'
)


def merge_waiting_into_manufacture(apps, schema_editor):
	"""Fold the standalone "Allow time for orders to arrive" waiting stage into
	the following "Manufacture Product" stage so they read as one step, and
	carry the waiting period's expected days across so the progress bar (now
	shown on the manufacture stage) still counts the full lead time."""
	WorkflowStage = apps.get_model('stock_take', 'WorkflowStage')
	WorkflowTask = apps.get_model('stock_take', 'WorkflowTask')
	WorkflowStageDate = apps.get_model('stock_take', 'WorkflowStageDate')
	OrderWorkflowProgress = apps.get_model('stock_take', 'OrderWorkflowProgress')

	waiting = WorkflowStage.objects.filter(name=WAITING_NAME, phase='sale').first()
	manufacture = WorkflowStage.objects.filter(name=MANUFACTURE_NAME, phase='sale').first()
	if not waiting or not manufacture:
		# Nothing to merge (e.g. a fresh DB seeded differently) — leave as-is.
		return

	# Move any orders currently sitting in the waiting stage onto the merged
	# manufacture stage so no order is orphaned.
	OrderWorkflowProgress.objects.filter(current_stage=waiting).update(current_stage=manufacture)

	# Reparent any tasks the waiting stage owned, appending after existing ones.
	existing_task_count = manufacture.tasks.count()
	for offset, task in enumerate(waiting.tasks.all().order_by('order', 'id')):
		task.stage = manufacture
		task.order = existing_task_count + offset
		task.save(update_fields=['stage', 'order'])

	# Move recorded completion dates, respecting the (order, stage) uniqueness —
	# if the order already has a manufacture date, drop the waiting one.
	manufacture_dated_orders = set(
		WorkflowStageDate.objects.filter(stage=manufacture).values_list('order_id', flat=True)
	)
	for sd in WorkflowStageDate.objects.filter(stage=waiting):
		if sd.order_id in manufacture_dated_orders:
			sd.delete()
		else:
			sd.stage = manufacture
			sd.save(update_fields=['stage'])

	# Combine the expected lead time and take the waiting stage's position in
	# the sequence so the merged step sits right after "Place Order".
	manufacture.expected_days = (waiting.expected_days or 0) + (manufacture.expected_days or 0)
	manufacture.order = waiting.order
	manufacture.description = MERGED_DESCRIPTION
	manufacture.save(update_fields=['expected_days', 'order', 'description'])

	waiting.delete()


class Migration(migrations.Migration):

	dependencies = [
		('stock_take', '0235_anthillsale_unique_non_blank_contract_number'),
	]

	operations = [
		migrations.RunPython(merge_waiting_into_manufacture, migrations.RunPython.noop),
	]
