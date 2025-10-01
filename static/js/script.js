$(document).ready(function() {
    let currentItemId = null;
    
    // Restock button functionality
    $('.restock-btn').on('click', function() {
        currentItemId = $(this).data('id');
        const itemName = $(this).data('name');
        
        $('#restockItemName').text(itemName);
        $('#restockQuantity').val(1);
        $('#restockModal').modal('show');
    });
    
    // Confirm restock
    $('#confirmRestock').on('click', function() {
        if (currentItemId) {
            const newQuantity = $('#restockQuantity').val();
            
            $.post('/update/' + currentItemId + '/', {
                'quantity': newQuantity,
                'csrfmiddlewaretoken': $('[name=csrfmiddlewaretoken]').val()
            })
            .done(function() {
                showNotification('Stock updated successfully!', 'success');
                setTimeout(() => location.reload(), 1000);
            })
            .fail(function() {
                showNotification('Error updating stock. Please try again.', 'error');
            });
        }
        
        $('#restockModal').modal('hide');
    });
    
    // Edit functionality
    $('.editable').on('click', function() {
        const cell = $(this);
        const field = cell.data('field');
        const itemId = cell.data('id');
        const currentValue = cell.text().replace('£', '').trim();
        
        if (cell.hasClass('editing')) return;
        
        cell.addClass('editing');
        const input = $('<input type="text" class="form-control form-control-sm">');
        input.val(currentValue);
        cell.html(input);
        input.focus();
        
        function saveEdit() {
            const newValue = input.val();
            
            $.post('/update/' + itemId + '/', {
                [field]: newValue,
                'csrfmiddlewaretoken': $('[name=csrfmiddlewaretoken]').val()
            })
            .done(function() {
                if (field === 'cost') {
                    cell.html('£' + parseFloat(newValue || 0).toFixed(2));
                } else {
                    cell.html(newValue);
                }
                cell.removeClass('editing');
                showNotification('Item updated successfully!', 'success');
                
                // If quantity was updated, check if page reload is needed
                if (field === 'quantity') {
                    setTimeout(() => location.reload(), 1000);
                }
            })
            .fail(function() {
                cell.html(currentValue);
                cell.removeClass('editing');
                showNotification('Error updating item. Please try again.', 'error');
            });
        }
        
        input.on('blur', saveEdit);
        input.on('keypress', function(e) {
            if (e.which === 13) {
                saveEdit();
            }
        });
    });
    
    // Notification system
    function showNotification(message, type) {
        const alertClass = type === 'success' ? 'alert-success' : 'alert-danger';
        const notification = `
            <div class="alert ${alertClass} alert-dismissible fade show position-fixed" 
                 style="top: 20px; right: 20px; z-index: 9999; min-width: 300px;">
                ${message}
                <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
            </div>
        `;
        
        $('body').append(notification);
        
        // Auto-dismiss after 3 seconds
        setTimeout(() => {
            $('.alert').alert('close');
        }, 3000);
    }
    
    // Table toggle functionality
    $('.table-toggle').on('click', function() {
        const targetTable = $(this).data('target');
        const $table = $(targetTable);
        const $icon = $(this).find('i');
        
        $table.collapse('toggle');
        
        $table.on('shown.bs.collapse', function() {
            $icon.removeClass('bi-chevron-down').addClass('bi-chevron-up');
        });
        
        $table.on('hidden.bs.collapse', function() {
            $icon.removeClass('bi-chevron-up').addClass('bi-chevron-down');
        });
    });
});