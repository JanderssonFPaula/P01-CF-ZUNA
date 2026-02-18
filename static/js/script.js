// Formatação de moeda
function formatarMoeda(valor) {
    return valor.toLocaleString('pt-BR', { 
        style: 'currency', 
        currency: 'BRL' 
    });
}

// Validação de formulários
document.addEventListener('DOMContentLoaded', function() {
    // Validar valores monetários
    const inputsValor = document.querySelectorAll('input[name="valor"], input[name="saldo"]');
    
    inputsValor.forEach(input => {
        input.addEventListener('input', function() {
            if (this.value < 0) {
                this.value = 0;
            }
        });
    });
    
    // Confirmação antes de deletar
    const btnsDeletar = document.querySelectorAll('button[type="submit"]');
    btnsDeletar.forEach(btn => {
        if (btn.classList.contains('btn-danger') && btn.textContent.includes('Deletar')) {
            btn.addEventListener('click', function(e) {
                const modal = this.closest('.modal');
                if (modal && !modal.id.includes('Deletar')) {
                    return;
                }
            });
        }
    });
    
    // Auto-focus em modais
    const modals = document.querySelectorAll('.modal');
    modals.forEach(modal => {
        modal.addEventListener('shown.bs.modal', function () {
            const input = this.querySelector('input[type="text"], input[type="number"]');
            if (input) {
                input.focus();
            }
        });
    });
});

// Feedback visual ao salvar
function mostrarFeedback(mensagem, tipo = 'success') {
    const alert = document.createElement('div');
    alert.className = `alert alert-${tipo} alert-dismissible fade show position-fixed top-0 start-50 translate-middle-x mt-3`;
    alert.style.zIndex = '9999';
    alert.innerHTML = `
        ${mensagem}
        <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
    `;
    
    document.body.appendChild(alert);
    
    setTimeout(() => {
        alert.remove();
    }, 3000);
}

// Animações suaves ao carregar
window.addEventListener('load', function() {
    document.body.style.opacity = '0';
    document.body.style.transition = 'opacity 0.3s';
    setTimeout(() => {
        document.body.style.opacity = '1';
    }, 100);
});
