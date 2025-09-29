document.addEventListener('DOMContentLoaded', function () {
    const chatForm = document.getElementById('chat-form');
    const chatInput = document.getElementById('chat-input');
    const chatBox = document.getElementById('chat-box');
    const markBtn = document.getElementById('mark-complete-btn');


    // Only load intro if chatBox is empty (i.e., first load, no chat yet)
    if (chatBox && chatBox.innerHTML.trim() === '') {
        fetch('/chatbot/module_intro', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ module_id: chatForm.dataset.moduleId })
        })
        .then(resp => resp.json())
        .then(data => {
            chatBox.innerHTML += `<div class="mb-2 text-left">
                <span class="inline-block px-3 py-2 bg-yellow-50 rounded-lg">${data.intro}</span>
            </div>`;
            chatBox.scrollTop = chatBox.scrollHeight;
        });
    }
        
    if (chatForm) {
        chatForm.addEventListener('submit', async function (e) {
            e.preventDefault();
            const message = chatInput.value;
            chatBox.innerHTML += `<div class="mb-2 text-right"><span class="inline-block px-3 py-2 bg-blue-100 rounded-lg">${message}</span></div>`;
            chatInput.value = '';
            // Post to backend
            const resp = await fetch('/chatbot/message', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    message: message,
                    module_id: chatForm.dataset.moduleId
                })
            });
            const data = await resp.json();
            chatBox.innerHTML += `<div class="mb-2 text-left"><span class="inline-block px-3 py-2 bg-gray-100 rounded-lg">${data.reply}</span></div>`;
            chatBox.scrollTop = chatBox.scrollHeight;

            // Detect MCQ and user's answer, simulate quiz answer logic
            // After receiving the response from /chatbot/message, handle quiz_passed directly
            if (
                message.trim().length === 1 &&
                "ABCD".includes(message.trim().toUpperCase()) &&
                data.hasOwnProperty('quiz_passed')
            ) {
                if (data.quiz_passed) {
                    chatBox.innerHTML += `<div class="mb-2 text-left"><span class="inline-block px-3 py-2 bg-green-100 text-green-800 rounded-lg">✅ Correct! You can now mark this module as complete.</span></div>`;
                    if (markBtn) {
                        markBtn.disabled = false;
                    }
                } else {
                    chatBox.innerHTML += `<div class="mb-2 text-left"><span class="inline-block px-3 py-2 bg-red-100 text-red-800 rounded-lg">❌ Incorrect, please try again.</span></div>`;
                }
                chatBox.scrollTop = chatBox.scrollHeight;
            }

        });
    }
    // Initially disable Mark Complete button if not passed
    if (markBtn && !window.quizPassed) {
        markBtn.disabled = true;
    }
});



  
