from django.db import migrations


STAGE_NAME = 'Arrange Install Date & Take Stock Payment'


def remove_attempt_tasks(apps, schema_editor):
	"""Drop the "1st/2nd attempt to arrange install date" checkboxes from the
	install-date stage. They're replaced on the stage card by two derived
	actions — "Add Payment" and "Book Fit Date" — which tick themselves from the
	real payment/fit-date data instead of being manually recorded."""
	WorkflowStage = apps.get_model('stock_take', 'WorkflowStage')
	WorkflowTask = apps.get_model('stock_take', 'WorkflowTask')

	stage = WorkflowStage.objects.filter(name=STAGE_NAME, phase='sale').first()
	if not stage:
		return

	WorkflowTask.objects.filter(
		stage=stage, description__icontains='attempt to arrange install date'
	).delete()


class Migration(migrations.Migration):

	dependencies = [
		('stock_take', '0247_supplier_payment_method'),
	]

	operations = [
		migrations.RunPython(remove_attempt_tasks, migrations.RunPython.noop),
	]
