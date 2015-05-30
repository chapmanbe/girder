/**
 * A dialog for creating thumbnails from a specific file.
 */
girder.views.thumbnails_CreateThumbnailView = girder.View.extend({
    events: {
        'change .g-thumbnail-attach-container input[type="radio"]': function () {
            if (this.$('.g-thumbnail-attach-this-item').is(':checked')) {
                this.attachToType = 'item';
                this.attachToId = this.item.id;
            } else {
                this.attachToType = null;
                this.attachToId = null;
            }
        },

        'submit #g-create-thumbnail-form': function (e) {
            e.preventDefault();

            this.$('.g-validation-failed-message').empty();
            this.$('.g-submit-create-thumbnail').attr('disabled', 'disabled');

            var thumbnail = new girder.models.ThumbnailModel({
                width: Number(this.$('#g-thumbnail-width').val()) || 0,
                height: Number(this.$('#g-thumbnail-height').val()) || 0,
                crop: this.$('#g-thumbnail-crop').is(':checked'),
                fileId: this.file.id,
                attachToId: this.attachToId,
                attachToType: this.attachToType
            }).on('g:saved', function () {
                this.$el.on('hidden.bs.modal', _.bind(function () {
                    this.trigger('g:created', {
                        attachedToType: this.attachToType,
                        attachedToId: this.attachToId
                    });
                }, this)).modal('hide');
            }, this).on('g:error', function (resp) {
                this.$('.g-submit-create-thumbnail').removeAttr('disabled');
                this.$('.g-validation-failed-message').text(resp.responseJSON.message);
            }, this).save();
        }
    },

    initialize: function (settings) {
        this.item = settings.item;
        this.file = settings.file;
        this.attachToType = 'item';
        this.attachToId = this.item.id;
    },

    render: function () {
        var view = this;
        this.$el.html(girder.templates.thumbnails_createDialog({
            file: this.file,
            item: this.item
        })).girderModal(this).on('shown.bs.modal', function () {
            view.$('#g-thumbnail-width').focus();
        });

        this.$('#g-thumbnail-width').focus();

        return this;
    }
});
